
"""
This wraps both torch.nn.functional and torch.functional, and other __torch_function__ on tensors.

Note that this all maps to modules, which will be temporarily created.
In RETURNN, every operation is a layer.
"""

import numpy
from typing import Optional, Union, List, Tuple, Dict, TypeVar, Sequence
from . import modules
from ..tensor import Tensor
from .._C import Size, dtype as _dtype


_number = Union[int, float, numpy.ndarray, numpy.number]
_size = Union[Size, List[int], Tuple[int, ...]]
_T = TypeVar("_T")


def cast(input: Union[_T, Tensor, _number], dtype: Union[str, _dtype]) -> Union[_T, Tensor]:
  dtype = _dtype(dtype)
  if dtype == get_dtype(input):
    return input
  return modules.Cast(dtype=dtype)(input)


def get_dtype(tensor: Union[Tensor, _number]) -> _dtype:
  if isinstance(tensor, Tensor):
    return tensor.dtype
  if isinstance(tensor, int):
    return _dtype("int32")
  if isinstance(tensor, float):
    return _dtype("float32")
  if isinstance(tensor, (numpy.number, numpy.ndarray)):
    return _dtype(str(tensor.dtype))
  raise TypeError(f"unexpected type {type(tensor)}")


def result_type(tensor1: Union[Tensor, _number], tensor2: Union[Tensor, _number]) -> _dtype:
  # https://pytorch.org/docs/stable/generated/torch.result_type.html
  return promote_types(get_dtype(tensor1), get_dtype(tensor2))


def promote_types(type1: Union[str, _dtype], type2: Union[str, _dtype]) -> _dtype:
  # https://pytorch.org/docs/stable/generated/torch.promote_types.html
  type1 = _dtype(type1)
  type2 = _dtype(type2)
  if type1.category_int != type2.category_int:
    if type1.category_int < type2.category_int:
      type1, type2 = type2, type1
    assert type1.category_int > type2.category_int
    return type1
  assert type1.category_int == type2.category_int
  if type1.bit_size == type2.bit_size:
    assert type1 == type2
    return type1
  if type1.bit_size < type2.bit_size:
    type1, type2 = type2, type1
  assert type1.bit_size > type2.bit_size
  return type1


def as_tensor(data: Union[Tensor, _number],
              dtype: Optional[Union[str, _dtype]] = None,
              device=None) -> Tensor:
  if not isinstance(data, Tensor):
    from .._C import from_numpy
    data = from_numpy(data)
  assert isinstance(data, Tensor)
  if dtype:
    data = cast(data, dtype)
  return data


def add(x: Tensor, y: Tensor) -> Tensor:
  dtype = result_type(x, y)
  return modules.BinaryOperator(kind="add")(cast(x, dtype), cast(y, dtype))


def sub(x: Tensor, y: Tensor) -> Tensor:
  dtype = result_type(x, y)
  return modules.BinaryOperator(kind="sub")(cast(x, dtype), cast(y, dtype))


def mul(x: Tensor, y: Tensor) -> Tensor:
  dtype = result_type(x, y)
  return modules.BinaryOperator(kind="mul")(cast(x, dtype), cast(y, dtype))


def truediv(x: Tensor, y: Tensor) -> Tensor:
  dtype = result_type(x, y)
  return modules.BinaryOperator(kind="truediv")(cast(x, dtype), cast(y, dtype))


def flatten(input: Tensor, start_dim=0, end_dim=-1) -> Tensor:
  return modules.Flatten(start_dim=start_dim, end_dim=end_dim).as_returnn_torch_functional()(input)


def reshape(input: Tensor, shape: Tuple[int, ...]) -> Tensor:
  if any(dim == -1 for dim in shape):
    num = input.numel()
    for dim in shape:
      if dim == -1:
        continue
      assert dim > 0 and num % dim == 0
      num //= dim
    shape = [dim if dim >= 0 else num for dim in shape]

  # Use Flatten, Unflatten, Squeeze.
  # (Other reshapes are disallowed.)
  axis1, axis2 = 0, 0
  while axis1 < len(input.shape) and axis2 < len(shape):
    if input.shape[axis1] == shape[axis2]:
      axis1 += 1
      axis2 += 1
      continue
    elif input.shape[axis1] < shape[axis2]:
      if input.shape[axis1] == 1:
        input = modules.Squeeze(dim=axis1).as_returnn_torch_functional()(input)
        continue
      n = 1
      a = axis1
      while a < len(input.shape) and n < shape[axis2]:
        assert shape[axis2] % n == 0 and n < shape[axis2]
        n *= input.shape[a]
        a += 1
      assert n == shape[axis2]
      input = modules.Flatten(start_dim=axis1, end_dim=a - 1).as_returnn_torch_functional()(input)
      assert input.shape[axis1] == shape[axis2]
      continue
    elif input.shape[axis1] > shape[axis2]:
      n = 1
      a = axis2
      while a < len(shape) and n < input.shape[axis1]:
        assert input.shape[axis1] % n == 0 and n < input.shape[axis1]
        n *= shape[a]
        a += 1
      assert n == input.shape[axis1]
      input = modules.Unflatten(dim=axis1, unflattened_size=tuple(shape[axis2:a])).as_returnn_torch_functional()(input)
      assert input.shape[axis1] == shape[axis2]
      continue
    else:
      assert False  # cannot happen
  assert axis1 == axis2
  if len(input.shape) < len(shape):
    assert all(shape[i] == 1 for i in range(len(input.shape), len(shape)))
    input = modules.Unflatten(
      dim=-1, unflattened_size=shape[len(input.shape) - 1:]).as_returnn_torch_functional()(input)
  elif len(input.shape) > len(shape):
    while len(input.shape) > len(shape):
      input = modules.Squeeze(dim=len(shape)).as_returnn_torch_functional()(input)
  assert len(input.shape) == len(shape) and input.shape == tuple(shape)
  return input


def movedim(input: Tensor, source: Union[int, Tuple[int, ...]], destination: Union[int, Tuple[int, ...]]):
  if isinstance(source, int):
    source = (source,)
  if isinstance(destination, int):
    destination = (destination,)
  assert isinstance(source, (tuple, list)) and isinstance(destination, (tuple, list))
  assert len(source) == len(destination)
  perm = {i: j for i, j in zip(destination, source)}
  # All remaining axes stay in order.
  return tensorflow_transpose(input, perm=perm)


def transpose(input: Tensor, dim0: int, dim1: int):
  return tensorflow_transpose(input, perm={dim0: dim1, dim1: dim0})


def tensorflow_transpose(input: Tensor, perm: Optional[Union[Dict[int, int], Tuple[int, ...], List[int]]]):
  """
  Note: This function is added by us, not available in original PyTorch.

  Note: The resulting Torch tensor is transposed as expected.
  However, on the RETURNN side, we actually should never need to transpose,
  as we have dimension tags, and all layers should refer to axes by dim tags.
  So on RETURNN side, this is a no-op.
  """
  return modules.Transpose(perm=perm)(input)


def pad(input: Tensor, pad, mode='constant', value=0) -> Tensor:
  return modules.GenericPadNd(padding=pad, mode=mode, value=value).as_returnn_torch_functional()(input)


def max(*inputs: Tensor) -> Tensor:
  return modules.Max()(*inputs)


def conv1d(
    input: Tensor, weight: Tensor, bias: Optional[Tensor] = None,
    stride: Union[int, _size] = 1, padding: Union[int, _size] = 0,
    dilation: Union[int, _size] = 1, groups: int = 1) -> Tensor:
  mod = modules.FunctionalConv1d(stride=stride, padding=padding, dilation=dilation, groups=groups)
  return mod(input, weight, bias)


def conv2d(
    input: Tensor, weight: Tensor, bias: Optional[Tensor] = None,
    stride: Union[int, _size] = 1, padding: Union[int, _size] = 0,
    dilation: Union[int, _size] = 1, groups: int = 1) -> Tensor:
  mod = modules.FunctionalConv2d(stride=stride, padding=padding, dilation=dilation, groups=groups)
  return mod(input, weight, bias)


def conv_transpose1d(
    input: Tensor, weight: Tensor, bias: Optional[Tensor] = None,
    stride: Union[int, _size] = 1, padding: Union[int, _size] = 0,
    output_padding: Union[int, _size] = 0,
    groups: int = 1,
    dilation: Union[int, _size] = 1) -> Tensor:
  mod = modules.FunctionalConvTransposed1d(
    stride=stride, padding=padding, output_padding=output_padding, dilation=dilation, groups=groups)
  return mod(input, weight, bias)


def max_pool2d(input: Tensor, kernel_size, stride=None, padding=0, dilation=1,
               ceil_mode=False, return_indices=False):
  mod = modules.MaxPool2d(
    kernel_size=kernel_size, stride=stride, padding=padding, dilation=dilation,
    ceil_mode=ceil_mode, return_indices=return_indices)
  mod.as_returnn_torch_functional()
  return mod(input)


def relu(input: Tensor) -> Tensor:
  return modules.ReLU().as_returnn_torch_functional()(input)


def leaky_relu(input: Tensor, negative_slope: float = 0.01, inplace: bool = False) -> Tensor:
  return modules.LeakyReLU(negative_slope=negative_slope, inplace=inplace).as_returnn_torch_functional()(input)


def tanh(input: Tensor) -> Tensor:
  return modules.Tanh().as_returnn_torch_functional()(input)


def softmax(input: Tensor, dim: Optional[int] = None, dtype=None):
  return modules.Softmax(dim=dim).as_returnn_torch_functional()(input)


def log_softmax(input: Tensor, dim: Optional[int] = None, dtype=None):
  return modules.LogSoftmax(dim=dim).as_returnn_torch_functional()(input)


def normalize(input: Tensor, p=2, dim=1, eps=1e-12) -> Tensor:
  norm_ = modules.Norm(p=p, axes=[dim], keepdims=True)(input)
  norm_f = modules.Reciprocal(eps=eps)(norm_)
  return input * norm_f


def norm(input: Tensor,
         p: Optional[Union[str, float, int]] = "fro",
         dim: Optional[Union[int, List[int]]] = None,
         keepdim: bool = False) -> Tensor:
  return modules.Norm(p=p, axes=[dim], keepdims=keepdim)(input)


def norm_except_dim(v: Tensor, pow: int = 2, dim: int = 0) -> Tensor:
  return modules.Norm(p=pow, axes=[i for i in range(v.dim()) if i != dim], keepdims=True)(v)
