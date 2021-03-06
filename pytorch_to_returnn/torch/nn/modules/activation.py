
import warnings
from typing import Dict, Any, Optional
from .module import Module
from ...tensor import Tensor


class _ActivationReturnn(Module):
  func_name: str

  def create_returnn_layer_dict(self, input: Tensor) -> Dict[str, Any]:
    return {"class": "activation", "activation": self.func_name, "from": self._get_input_layer_name(input)}


class ReLU(_ActivationReturnn):
  func_name = "relu"


class Tanh(_ActivationReturnn):
  func_name = "tanh"


class LeakyReLU(Module):
  def __init__(self, negative_slope: float = 1e-2, inplace: bool = False) -> None:
    super(LeakyReLU, self).__init__()
    self.negative_slope = negative_slope
    assert not inplace  # not supported/implemented -- see :doc:`Unsupported`

  def create_returnn_layer_dict(self, input: Tensor) -> Dict[str, Any]:
    return {
      "class": "eval", "eval": f"tf.nn.leaky_relu(source(0), alpha={self.negative_slope})",
      "from": self._get_input_layer_name(input)}


class Softmax(Module):
  _name = "softmax"

  def __init__(self, dim: Optional[int] = None) -> None:
    super(Softmax, self).__init__()
    self.dim = dim

  @classmethod
  def _get_default_softmax_dim(cls, *, ndim: int) -> int:
    warnings.warn(f"Implicit dimension choice for {cls._name} has been deprecated. "
                  "Change the call to include dim=X as an argument.", stacklevel=2)
    if ndim == 0 or ndim == 1 or ndim == 3:
      return 0
    else:
      return 1

  def create_returnn_layer_dict(self, input: Tensor) -> Dict[str, Any]:
    if self.dim is not None:
      dim = self.dim
    else:
      dim = self._get_default_softmax_dim(ndim=input.ndim)
    returnn_axis = self._get_input_axis_to_returnn(input, dim)
    if returnn_axis == "F":
      return {"class": "activation", "activation": self._name, "from": self._get_input_layer_name(input)}
    return {
      "class": "softmax_over_spatial", "axis": returnn_axis, "from": self._get_input_layer_name(input),
      "log_space": {"softmax": False, "log_softmax": True}[self._name]}


class LogSoftmax(Softmax):
  _name = "log_softmax"


__all__ = [
  key for (key, value) in sorted(globals().items())
  if not key.startswith("_")
  and getattr(value, "__module__", "") == __name__]
