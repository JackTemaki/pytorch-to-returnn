
from __future__ import annotations
from typing import Optional, List, Tuple, Union, Any, Dict
from tensorflow.python.util import nest
from returnn.tf.layers.basic import LayerBase
from . import _types
from . import module as _module
from . import naming as _naming
from . import tensor as _tensor
from . import namespace as _namespace


class CallEntry:
  """
  Can be a module() call, or regular func.
  Note that a module can be called multiple times.
  """
  module: _module.ModuleEntry
  orig_inputs_args: Optional[Tuple[Union[_types.Tensor, Any]]] = None
  orig_inputs_kwargs: Optional[Dict[str, Tuple[_types.Tensor, Any]]] = None
  orig_inputs_flat: Optional[List[Tuple[_types.Tensor, Any]]] = None
  orig_outputs: Optional[Union[_types.Tensor, Tuple[_types.Tensor]]] = None
  orig_outputs_flat: Optional[List[Union[_types.Tensor, Any]]] = None
  inputs_args: Optional[Tuple[Optional[_tensor.TensorEntry]]] = None
  inputs_kwargs: Optional[Dict[str, Optional[Tuple[_tensor.TensorEntry, Any]]]] = None
  inputs_flat: Optional[List[_tensor.TensorEntry]] = None
  outputs: Optional[List[_tensor.TensorEntry]] = None
  outputs_flat: Optional[List[_tensor.TensorEntry]] = None
  parent_call: Optional[CallEntry] = None  # parent in the call stack
  child_calls: List[CallEntry]
  level: Optional[int] = None
  namespace: Optional[_namespace.RegisteredName] = None
  returnn_layer: Optional[LayerBase] = None
  returnn_layer_dict: Optional[Dict[str, Any]] = None

  def __init__(self, module: _module.ModuleEntry):
    self.module = module
    module.calls.append(self)
    self.child_calls = []

  def __repr__(self):
    return f"<{self.__class__.__name__} #{self.level} {self.module!r}>"

  def get_root_call(self) -> CallEntry:
    entry = self
    while entry.parent_call:
      entry = entry.parent_call
    return entry

  def get_canonical_name(self) -> str:
    """
    Considering the canonical context where this is being used.
    Not an absolute name but relative.
    """
    return self.module.get_canonical_name(parent_namespace=self.namespace.parent)

  def set_returnn_layer(self, layer: Optional[LayerBase], layer_dict: Optional[Dict[str, Any]]):
    self.returnn_layer = layer
    self.returnn_layer_dict = layer_dict

  def set_outputs(self, outputs: Union[_types.Tensor, Tuple[_types.Tensor], List[_types.Tensor]]):
    assert self.outputs is None
    naming = _naming.Naming.get_instance()
    if naming.keep_orig_module_io_tensors:
      self.orig_outputs = outputs
    if naming.wrap_to_returnn_enabled:  # not all tensors are traced currently otherwise. also not needed
      if not isinstance(outputs, (list, tuple)):
        outputs = [outputs]
      entry_outputs = [naming.tensors[x] for x in outputs]
      self.outputs = entry_outputs
      for x in entry_outputs:
        x.output_from_calls.append(self)
        if self.module:
          x.output_from_modules.append(self.module)
      if entry_outputs:
        if self.namespace not in entry_outputs[0].names:
          entry_outputs[0].names.append(self.namespace)

  def apply_call(self) -> _types.Tensor:
    from pytorch_to_returnn.torch.nn import Module
    from pytorch_to_returnn.torch import Tensor
    from returnn.tf.util.basic import reuse_name_scope
    naming = _naming.Naming.get_instance()
    module = self.module.module
    assert self.namespace
    inputs_flat = [x.tensor() if x else None for x in self.inputs_flat]  # make sure all are tensors
    inputs_args, inputs_kwargs = nest.pack_sequence_as(
      structure=(self.inputs_args, self.inputs_kwargs), flat_sequence=inputs_flat)

    if module.has_torch_forward():
      for x in inputs_flat:
        self.namespace.register_input(tensor=naming.tensors[x])
      res = module.forward(*inputs_args)
      assert isinstance(res, Tensor)  # TODO only single output supported currently...
      res_entry = naming.tensors[res]
      if self.namespace.returnn_ctx.sub_net_layer:
        self.namespace.register_returnn_subnet_output(res_entry)
      layer = self.namespace.returnn_ctx.sub_net_layer
      layer_dict = None  # will be constructed later lazily when needed

    else:  # no module.forward, direct RETURNN layer call
      assert module.create_returnn_layer_dict is not Module.create_returnn_layer_dict
      assert self.namespace and self.namespace.parent
      parent_namespace = self.namespace.parent
      parent_namespace.maybe_create_returnn_ctx()
      layer_dict = module.create_returnn_layer_dict(*inputs_args, **inputs_kwargs)
      layer_name = self.namespace.name
      returnn_net = parent_namespace.returnn_ctx.network
      assert layer_name not in returnn_net.layers
      if len(self.module.calls) >= 2:
        raise NotImplementedError  # would need to set reuse_params ...
      print(f"*** {returnn_net.name}/{layer_name!r} layer dict: {layer_dict}")

      # Now the main construction of the layer itself.
      with reuse_name_scope(parent_namespace.returnn_ctx.tf_name_scope, absolute=True):
        layer = returnn_net.construct_layer(net_dict={layer_name: layer_dict}, name=layer_name)

      # Update params in parents.
      parent_layer = layer.network.parent_layer
      parent_layer_param_prefix = f"{layer.name}/"
      while parent_layer:
        if parent_layer.name.startswith("."):
          break  # stop if hidden
        parent_layer.params.update({parent_layer_param_prefix + k: v for (k, v) in layer.params.items()})
        parent_layer_param_prefix = f"{parent_layer.name}/{parent_layer_param_prefix}"
        parent_layer = parent_layer.network.parent_layer

      module.check_returnn_layer(layer)
      res = module.make_output_tensor_from_returnn(inputs_flat=inputs_flat, layer=layer)
      res_entry = naming.tensors[res]
      assert isinstance(res_entry, _tensor.TensorEntry)
      res_entry.returnn_data = layer.output
      self.namespace.assign_tensor(res_entry)

    assert isinstance(res, Tensor)
    self.set_returnn_layer(layer=layer, layer_dict=layer_dict)
    self.set_outputs([res])

    if layer:  # might not exist in the root namespace
      layer_abs_repr_name = f"{layer.network.name}/{layer.name!r}"
      print(
        f"*** {layer_abs_repr_name} {layer.__class__.__name__} output: "
        f"[{','.join(layer.output.get_batch_axes_short_description())}]")

      if naming.import_params_from_torch_namespace and layer:
        if not layer_abs_repr_name.startswith("."):  # temp layer
          if module.is_original_torch_module:
            if list(module.parameters(recurse=False)):
              mod_abs_name = naming.get_module_abs_name(module)
              torch_mod = naming.import_params_from_torch_namespace.get_module_by_abs_name(mod_abs_name)
              print(
                f"*** {layer_abs_repr_name} {layer.__class__.__name__} "
                f"importing params {[name for name, _ in module.named_parameters(recurse=False)]} ...")
              module.import_params_torch_to_returnn(layer=layer, torch_module=torch_mod)

            print(
              f"*** {layer_abs_repr_name} {layer.__class__.__name__} "
              f"check RETURNN inputs/outputs given Torch inputs/outputs ...")
            module.check_call_returnn_outputs_to_prev_torch(self)

    return res

  def __enter__(self):
    # Assume via push_func_call
    assert _naming.Naming.get_instance().module_call_stack[-1] is self
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    if not exc_type:
      _naming.Naming.get_instance().pop_module_call(self)
