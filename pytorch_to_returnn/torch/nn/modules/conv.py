
import math
from typing import Optional, Dict, Any
from .module import Module
from .utils import _single, _pair, _triple, _reverse_repeat_tuple
from ..common_types import _size_1_t, _size_2_t, _size_3_t
from ...tensor import Tensor
from ..parameter import Parameter
from .. import init


class _ConvNd(Module):
  def __init__(self,
               in_channels: int,
               out_channels: int,
               kernel_size: _size_1_t,
               stride: _size_1_t,
               padding: _size_1_t,
               dilation: _size_1_t,
               transposed: bool,
               output_padding: _size_1_t,
               groups: int,
               bias: bool,
               padding_mode: str) -> None:
    super(_ConvNd, self).__init__()
    if in_channels % groups != 0:
      raise ValueError('in_channels must be divisible by groups')
    if out_channels % groups != 0:
      raise ValueError('out_channels must be divisible by groups')
    valid_padding_modes = {'zeros', 'reflect', 'replicate', 'circular'}
    if padding_mode not in valid_padding_modes:
      raise ValueError("padding_mode must be one of {}, but got padding_mode='{}'".format(
        valid_padding_modes, padding_mode))
    self.in_channels = in_channels
    self.out_channels = out_channels
    self.kernel_size = kernel_size
    self.stride = stride
    self.padding = padding
    self.dilation = dilation
    self.transposed = transposed
    self.output_padding = output_padding
    self.groups = groups
    self.padding_mode = padding_mode
    # `_reversed_padding_repeated_twice` is the padding to be passed to
    # `F.pad` if needed (e.g., for non-zero padding types that are
    # implemented as two ops: padding + conv). `F.pad` accepts paddings in
    # reverse order than the dimension.
    self._reversed_padding_repeated_twice = _reverse_repeat_tuple(self.padding, 2)
    if transposed:
      self.weight = Parameter(Tensor(
        in_channels, out_channels // groups, *kernel_size))
    else:
      self.weight = Parameter(Tensor(
        out_channels, in_channels // groups, *kernel_size))
    if bias:
      self.bias = Parameter(Tensor(out_channels))
    else:
      self.register_parameter('bias', None)
    self.reset_parameters()

  def reset_parameters(self) -> None:
    init.kaiming_uniform_(self.weight, a=math.sqrt(5))
    if self.bias is not None:
      fan_in, _ = init._calculate_fan_in_and_fan_out(self.weight)
      bound = 1 / math.sqrt(fan_in)
      init.uniform_(self.bias, -bound, bound)

  def create_returnn_layer_dict(self, input: str) -> Dict[str, Any]:
    assert self.groups == 1  # not implemented otherwise
    assert self.padding == 0  # not implemented otherwise
    assert self.padding_mode == "zeros"  # not implemented otherwise
    return {
      "class": "conv", "from": input,
      "n_out": self.out_channels,
      "filter_size": self.kernel_size,
      "padding": "valid",
      "strides": self.stride,
      "dilation_rate": self.dilation}

  def param_import_torch_to_returnn(self, layer):
    # TODO {"weight": "W", "bias": "bias"}
    # TODO transpose ...
    pass


class Conv1d(_ConvNd):
  def __init__(
      self,
      in_channels: int,
      out_channels: int,
      kernel_size: _size_1_t,
      stride: _size_1_t = 1,
      padding: _size_1_t = 0,
      dilation: _size_1_t = 1,
      groups: int = 1,
      bias: bool = True,
      padding_mode: str = 'zeros'
  ):
    kernel_size = _single(kernel_size)
    stride = _single(stride)
    padding = _single(padding)
    dilation = _single(dilation)
    super(Conv1d, self).__init__(
      in_channels, out_channels, kernel_size, stride, padding, dilation,
      False, _single(0), groups, bias, padding_mode)


class Conv2d(Module):
  pass


class _ConvTransposeNd(_ConvNd):
  def __init__(self, in_channels, out_channels, kernel_size, stride,
               padding, dilation, transposed, output_padding,
               groups, bias, padding_mode):
    if padding_mode != 'zeros':
      raise ValueError('Only "zeros" padding mode is supported for {}'.format(self.__class__.__name__))

    super(_ConvTransposeNd, self).__init__(
      in_channels, out_channels, kernel_size, stride,
      padding, dilation, transposed, output_padding,
      groups, bias, padding_mode)


class ConvTranspose1d(_ConvTransposeNd):
  def __init__(
      self,
      in_channels: int,
      out_channels: int,
      kernel_size: _size_1_t,
      stride: _size_1_t = 1,
      padding: _size_1_t = 0,
      output_padding: _size_1_t = 0,
      groups: int = 1,
      bias: bool = True,
      dilation: _size_1_t = 1,
      padding_mode: str = 'zeros'
  ):
    kernel_size = _single(kernel_size)
    stride = _single(stride)
    padding = _single(padding)
    dilation = _single(dilation)
    output_padding = _single(output_padding)
    super(ConvTranspose1d, self).__init__(
      in_channels, out_channels, kernel_size, stride, padding, dilation,
      True, output_padding, groups, bias, padding_mode)


__all__ = [
  key for (key, value) in sorted(globals().items())
  if not key.startswith("_")
  and getattr(value, "__module__", "") == __name__]
