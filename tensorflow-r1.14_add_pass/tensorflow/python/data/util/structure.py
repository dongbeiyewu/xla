# Copyright 2018 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Utilities for describing the structure of a `tf.data` type."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import abc

import six

from tensorflow.python.data.util import nest
from tensorflow.python.framework import dtypes
from tensorflow.python.framework import ops
from tensorflow.python.framework import sparse_tensor as sparse_tensor_lib
from tensorflow.python.framework import tensor_shape
from tensorflow.python.framework import tensor_spec
from tensorflow.python.framework import tensor_util
from tensorflow.python.ops import list_ops
from tensorflow.python.ops import sparse_ops
from tensorflow.python.ops import tensor_array_ops
from tensorflow.python.ops.ragged import ragged_tensor
from tensorflow.python.ops.ragged import ragged_tensor_value
from tensorflow.python.util.tf_export import tf_export


_STRUCTURE_CONVERSION_FUNCTION_REGISTRY = {}


@tf_export("data.experimental.Structure")
@six.add_metaclass(abc.ABCMeta)
class Structure(object):
  """Represents structural information, such as type and shape, about a value.

  A `Structure` generalizes the `tf.Tensor.dtype` and `tf.Tensor.shape`
  properties, so that we can define generic containers of objects including:

  * `tf.Tensor`
  * `tf.SparseTensor`
  * Nested structures of the above.

  TODO(b/110122868): In the future, a single `Structure` will replace the
  `tf.data.Dataset.output_types`, `tf.data.Dataset.output_shapes`,
  and `tf.data.Dataset.output_classes`, and similar properties and arguments in
  the `tf.compat.v1.data.Iterator` and `Optional` classes.
  """

  @abc.abstractmethod
  def __eq__(self, other):
    """Returns the this structure and the input structure are equal.

    Args:
      other: the structure to use for equality check

    Returns:
      `True` if this and the input structure are equal and `False` otherwise.
    """
    raise NotImplementedError("Structure.__eq__()")

  def __ne__(self, other):
    return not self == other

  @abc.abstractmethod
  def __hash__(self):
    """Returns the hash of this structure.

    Returns:
      The hash of this structure.
    """
    raise NotImplementedError("Structure.__hash__()")

  @abc.abstractproperty
  def _flat_shapes(self):
    """A list of shapes matching the shapes of `self._to_tensor_list()`.

    Returns:
      A list of `tf.TensorShape` objects.
    """
    raise NotImplementedError("Structure._flat_shapes")

  @abc.abstractproperty
  def _flat_types(self):
    """A list of types matching the types of `self._to_tensor_list()`.

    Returns:
      A list of `tf.DType` objects.
    """
    raise NotImplementedError("Structure._flat_shapes")

  @abc.abstractmethod
  def is_compatible_with(self, other):
    """Returns `True` if `other` is compatible with this structure.

    A structure `t` is a "subtype" of `s` if:

    * `s` and `t` are instances of the same `Structure` subclass.
    * The nested structures (if any) of `s` and `t` are the same, according to
      `tf.nest.assert_same_structure`, and each nested
      structure of `t` is a "subtype" of the corresponding nested structure of
      `s`.
    * Any `tf.DType` components of `t` are the same as the corresponding
      components in `s`.
    * Any `tf.TensorShape` components of `t` are compatible with the
      corresponding components in `s`, according to
      `tf.TensorShape.is_compatible_with`.

    Args:
      other: A `Structure`.

    Returns:
      `True` if `other` is a subtype of this structure, otherwise `False`.
    """
    raise NotImplementedError("Structure.is_compatible_with()")

  @abc.abstractmethod
  def _to_tensor_list(self, value):
    """Returns a flat list of `tf.Tensor` representing `value`.

    This method can be used, along with `self._flat_shapes` and
    `self._flat_types` to represent structured values in lower level APIs
    (such as plain TensorFlow operations) that do not understand structure.

    Requires: `self.is_compatible_with(Structure.from_value(value))`.

    Args:
      value: A value with compatible structure.

    Returns:
      A flat list of `tf.Tensor` representing `value`.
    """
    raise NotImplementedError("Structure._to_tensor_list()")

  @abc.abstractmethod
  def _to_batched_tensor_list(self, value):
    """Returns a flat list of rank >= 1 `tf.Tensor` representing `value`.

    This method can be used, along with `self._flat_shapes` and
    `self._flat_types` to represent structured values in lower level APIs
    (such as plain TensorFlow operations) that do not understand structure,
    *and* that require that the plain tensors have a rank of at least one
    (e.g. for the purpose of slicing the tensors).

    Requires: `self.is_compatible_with(Structure.from_value(value))`.

    Args:
      value: A value with compatible structure.

    Returns:
      A flat list of `tf.Tensor` representing `value`.
    """
    raise NotImplementedError("Structure._to_batched_tensor_list()")

  @abc.abstractmethod
  def _from_tensor_list(self, flat_value):
    """Builds a flat list of `tf.Tensor` into a value matching this structure.

    Args:
      flat_value: A list of `tf.Tensor` with compatible flat structure.

    Returns:
      A structured object matching this structure.

    Raises:
      ValueError: If the shapes and types of the tensors in `flat_value` are not
        compatible with `self._flat_shapes` and `self._flat_types` respectively.
    """
    raise NotImplementedError("Structure._from_tensor_list()")

  def _from_compatible_tensor_list(self, flat_value):
    """A version of `_from_tensor_list()` that may avoid performing checks.

    NOTE: This method should be used to avoid checks for performance reasons,
    when the validity of `flat_value` has been validated by other means.
    The shapes and types of the tensors in `flat_value` must be compatible with
    `self._flat_shapes` and `self._flat_types` respectively. The behavior is
    undefined if this requirement is not met.

    Args:
      flat_value: A list of `tf.Tensor` with compatible flat structure.

    Returns:
      A structured object matching this structure.
    """
    return self._from_tensor_list(flat_value)

  @abc.abstractmethod
  def _batch(self, batch_size):
    """Returns a structure representing a batch of objects with this structure.

    Args:
      batch_size: An `int` representing the number of elements in a batch,
        or `None` if the batch size may vary.

    Returns:
      A `Structure` representing a batch of objects with this structure.
    """
    raise NotImplementedError("Structure._batch()")

  @abc.abstractmethod
  def _unbatch(self):
    raise NotImplementedError("Structure._unbatch()")

  @staticmethod
  def from_value(value):
    """Returns a `Structure` that represents the given `value`.

    Args:
      value: A potentially structured value.

    Returns:
      A `Structure` that is compatible with `value`.

    Raises:
      TypeError: If a structure cannot be built for `value`, because its type
        or one of its component types is not supported.
    """
    # TODO(b/110122868): Add support for custom types and Dataset to this
    # method.
    if isinstance(
        value,
        (sparse_tensor_lib.SparseTensor, sparse_tensor_lib.SparseTensorValue)):
      return SparseTensorStructure.from_value(value)
    elif isinstance(value, tensor_array_ops.TensorArray):
      return TensorArrayStructure.from_value(value)
    elif isinstance(
        value,
        (ragged_tensor.RaggedTensor, ragged_tensor_value.RaggedTensorValue)):
      return RaggedTensorStructure.from_value(value)
    elif isinstance(value, (tuple, dict)):
      return NestedStructure.from_value(value)
    else:
      for converter_type, converter_fn in (
          _STRUCTURE_CONVERSION_FUNCTION_REGISTRY.items()):
        if isinstance(value, converter_type):
          return converter_fn(value)
      try:
        tensor = ops.convert_to_tensor(value)
      except (ValueError, TypeError):
        raise TypeError("Could not build a structure for %r" % value)
      return TensorStructure.from_value(tensor)

  @staticmethod
  def _register_custom_converter(type_object, converter_fn):
    """Registers `converter_fn` for converting values of the given type.

    Args:
      type_object: A Python `type` object representing the type of values
        accepted by `converter_fn`.
      converter_fn: A function that takes one argument (an instance of the
        type represented by `type_object`) and returns a `Structure`.
    """
    _STRUCTURE_CONVERSION_FUNCTION_REGISTRY[type_object] = converter_fn

  @abc.abstractmethod
  def _to_legacy_output_types(self):
    raise NotImplementedError("Structure._to_legacy_output_types()")

  @abc.abstractmethod
  def _to_legacy_output_shapes(self):
    raise NotImplementedError("Structure._to_legacy_output_shapes()")

  @abc.abstractmethod
  def _to_legacy_output_classes(self):
    raise NotImplementedError("Structure._to_legacy_output_classes()")


def normalize_tensors(tensors):
  """Converts a nested structure of tensor-like objects to tensors.

  * `SparseTensor`-like inputs are converted to `SparseTensor`.
  * `TensorArray` inputs are passed through.
  * Everything else is converted to a dense `Tensor`.

  Args:
    tensors: A nested structure of tensor-like, list,
      `SparseTensor`, `SparseTensorValue`, or `TensorArray` objects.

  Returns:
    A nested structure of tensor, `SparseTensor`, or `TensorArray` objects.
  """
  flat_tensors = nest.flatten(tensors)
  prepared = []
  with ops.name_scope("normalize_tensors"):
    for i, t in enumerate(flat_tensors):
      if sparse_tensor_lib.is_sparse(t):
        prepared.append(sparse_tensor_lib.SparseTensor.from_value(t))
      elif ragged_tensor.is_ragged(t):
        prepared.append(
            ragged_tensor.convert_to_tensor_or_ragged_tensor(
                t, name="component_%d" % i))
      elif isinstance(t, tensor_array_ops.TensorArray):
        prepared.append(t)
      else:
        prepared.append(ops.convert_to_tensor(t, name="component_%d" % i))
  return nest.pack_sequence_as(tensors, prepared)


def convert_legacy_structure(output_types, output_shapes, output_classes):
  """Returns a `Structure` that represents the given legacy structure.

  This method provides a way to convert from the existing `Dataset` and
  `Iterator` structure-related properties to a `Structure` object. A "legacy"
  structure is represented by the `tf.data.Dataset.output_types`,
  `tf.data.Dataset.output_shapes`, and `tf.data.Dataset.output_classes`
  properties.

  TODO(b/110122868): Remove this function once `Structure` is used throughout
  `tf.data`.

  Args:
    output_types: A nested structure of `tf.DType` objects corresponding to
      each component of a structured value.
    output_shapes: A nested structure of `tf.TensorShape` objects
      corresponding to each component a structured value.
    output_classes: A nested structure of Python `type` objects corresponding
      to each component of a structured value.

  Returns:
    A `Structure`.

  Raises:
    TypeError: If a structure cannot be built from the arguments, because one of
      the component classes in `output_classes` is not supported.
  """
  flat_types = nest.flatten(output_types)
  flat_shapes = nest.flatten(output_shapes)
  flat_classes = nest.flatten(output_classes)
  flat_ret = []
  for flat_type, flat_shape, flat_class in zip(flat_types, flat_shapes,
                                               flat_classes):
    if isinstance(flat_class, Structure):
      flat_ret.append(flat_class)
    elif issubclass(flat_class, sparse_tensor_lib.SparseTensor):
      flat_ret.append(SparseTensorStructure(flat_type, flat_shape))
    elif issubclass(flat_class, ops.Tensor):
      flat_ret.append(TensorStructure(flat_type, flat_shape))
    elif issubclass(flat_class, tensor_array_ops.TensorArray):
      # We sneaked the dynamic_size and infer_shape into the legacy shape.
      flat_ret.append(
          TensorArrayStructure(
              flat_type, flat_shape[2:],
              dynamic_size=tensor_shape.dimension_value(flat_shape[0]),
              infer_shape=tensor_shape.dimension_value(flat_shape[1])))
    else:
      # NOTE(mrry): Since legacy structures produced by iterators only
      # comprise Tensors, SparseTensors, and nests, we do not need to
      # support all structure types here.
      raise TypeError(
          "Could not build a structure for output class %r" % (flat_class,))

  ret = nest.pack_sequence_as(output_classes, flat_ret)
  if isinstance(ret, Structure):
    return ret
  else:
    return NestedStructure(ret)


# NOTE(mrry): The following classes make extensive use of non-public methods of
# their base class, so we disable the protected-access lint warning once here.
# pylint: disable=protected-access
@tf_export("data.experimental.NestedStructure")
class NestedStructure(Structure):
  """Represents a nested structure in which each leaf is a `Structure`."""

  def __init__(self, nested_structure):
    self._nested_structure = nested_structure
    self._flat_nested_structure = nest.flatten(nested_structure)
    self._flat_shapes_list = []
    self._flat_types_list = []
    for s in nest.flatten(nested_structure):
      if not isinstance(s, Structure):
        raise TypeError("nested_structure must be a (potentially nested) tuple "
                        "or dictionary of Structure objects.")
      self._flat_shapes_list.extend(s._flat_shapes)
      self._flat_types_list.extend(s._flat_types)

  def __eq__(self, other):
    if not isinstance(other, NestedStructure):
      return False
    try:
      # pylint: disable=protected-access
      nest.assert_same_structure(self._nested_structure,
                                 other._nested_structure)
    except (ValueError, TypeError):
      return False

    return nest.flatten(self._nested_structure) == nest.flatten(
        other._nested_structure)

  def __hash__(self):
    return hash(tuple(nest.flatten(self._nested_structure)))

  @property
  def _flat_shapes(self):
    return self._flat_shapes_list

  @property
  def _flat_types(self):
    return self._flat_types_list

  def is_compatible_with(self, other):
    if not isinstance(other, NestedStructure):
      return False
    try:
      # pylint: disable=protected-access
      nest.assert_same_structure(self._nested_structure,
                                 other._nested_structure)
    except (ValueError, TypeError):
      return False

    return all(
        substructure.is_compatible_with(other_substructure)
        for substructure, other_substructure in zip(
            nest.flatten(self._nested_structure),
            nest.flatten(other._nested_structure)))

  def _to_tensor_list(self, value):
    ret = []

    try:
      flat_value = nest.flatten_up_to(self._nested_structure, value)
    except (ValueError, TypeError):
      raise ValueError("The value %r is not compatible with the nested "
                       "structure %r." % (value, self._nested_structure))

    for sub_value, structure in zip(flat_value, self._flat_nested_structure):
      if not structure.is_compatible_with(Structure.from_value(sub_value)):
        raise ValueError("Component value %r is not compatible with the nested "
                         "structure %r." % (sub_value, structure))
      ret.extend(structure._to_tensor_list(sub_value))
    return ret

  def _to_batched_tensor_list(self, value):
    ret = []

    try:
      flat_value = nest.flatten_up_to(self._nested_structure, value)
    except (ValueError, TypeError):
      raise ValueError("The value %r is not compatible with the nested "
                       "structure %r." % (value, self._nested_structure))

    for sub_value, structure in zip(flat_value, self._flat_nested_structure):
      if not structure.is_compatible_with(Structure.from_value(sub_value)):
        raise ValueError("Component value %r is not compatible with the nested "
                         "structure %r." % (sub_value, structure))
      ret.extend(structure._to_batched_tensor_list(sub_value))
    return ret

  def _from_tensor_list(self, flat_value):
    if len(flat_value) != len(self._flat_types):
      raise ValueError("Expected %d flat values in NestedStructure but got %d."
                       % (len(self._flat_types), len(flat_value)))

    flat_ret = []
    i = 0
    for structure in self._flat_nested_structure:
      num_flat_values = len(structure._flat_types)
      sub_value = flat_value[i:i + num_flat_values]
      flat_ret.append(structure._from_tensor_list(sub_value))
      i += num_flat_values

    return nest.pack_sequence_as(self._nested_structure, flat_ret)

  def _from_compatible_tensor_list(self, flat_value):
    flat_ret = []
    i = 0
    for structure in self._flat_nested_structure:
      num_flat_values = len(structure._flat_types)
      sub_value = flat_value[i:i + num_flat_values]
      flat_ret.append(structure._from_compatible_tensor_list(sub_value))
      i += num_flat_values

    return nest.pack_sequence_as(self._nested_structure, flat_ret)

  @staticmethod
  def from_value(value):
    flat_nested_structure = [
        Structure.from_value(sub_value) for sub_value in nest.flatten(value)
    ]
    return NestedStructure(nest.pack_sequence_as(value, flat_nested_structure))

  def _to_legacy_output_types(self):
    return nest.map_structure(
        lambda s: s._to_legacy_output_types(), self._nested_structure)

  def _to_legacy_output_shapes(self):
    return nest.map_structure(
        lambda s: s._to_legacy_output_shapes(), self._nested_structure)

  def _to_legacy_output_classes(self):
    return nest.map_structure(
        lambda s: s._to_legacy_output_classes(), self._nested_structure)

  def _batch(self, batch_size):
    return NestedStructure(nest.map_structure(
        lambda s: s._batch(batch_size), self._nested_structure))

  def _unbatch(self):
    return NestedStructure(nest.map_structure(
        lambda s: s._unbatch(), self._nested_structure))


@tf_export("data.experimental.TensorStructure")
class TensorStructure(Structure):
  """Represents structural information about a `tf.Tensor`."""

  def __init__(self, dtype, shape):
    self._dtype = dtypes.as_dtype(dtype)
    self._shape = tensor_shape.as_shape(shape)

  def __eq__(self, other):
    return (isinstance(other, TensorStructure) and tensor_spec.TensorSpec(
        self._shape, self._dtype) == tensor_spec.TensorSpec(
            other._shape, other._dtype))

  def __hash__(self):
    return hash(tensor_spec.TensorSpec(self._shape, self._dtype))

  @property
  def _flat_shapes(self):
    return [self._shape]

  @property
  def _flat_types(self):
    return [self._dtype]

  def is_compatible_with(self, other):
    return (isinstance(other, TensorStructure) and
            self._dtype.is_compatible_with(other._dtype) and
            self._shape.is_compatible_with(other._shape))

  def _to_tensor_list(self, value):
    if not self.is_compatible_with(Structure.from_value(value)):
      raise ValueError("Value %r is not convertible to a tensor with dtype %s "
                       "and shape %s." % (value, self._dtype, self._shape))
    return [value]

  def _to_batched_tensor_list(self, value):
    if self._shape.merge_with(value.shape).ndims == 0:
      raise ValueError("Unbatching a tensor is only supported for rank >= 1")
    return [value]

  def _from_tensor_list(self, flat_value):
    if len(flat_value) != 1:
      raise ValueError("TensorStructure corresponds to a single tf.Tensor.")
    if not self.is_compatible_with(Structure.from_value(flat_value[0])):
      raise ValueError("Cannot convert %r to a tensor with dtype %s and shape "
                       "%s." % (flat_value[0], self._dtype, self._shape))
    return self._from_compatible_tensor_list(flat_value)

  def _from_compatible_tensor_list(self, flat_value):
    # TODO(b/112266545): It would be cleaner to create a new `ensure_shape()`
    # op here and return that, instead of mutating the input's shape using
    # `Tensor.set_shape()`. However, that would add extra ops on the arguments
    # of each `tf.data` function, which could impact performance. When this
    # bug is resolved, we should be able to add the `ensure_shape()` ops and
    # optimize them away using contextual shape information.
    flat_value[0].set_shape(self._shape)
    return flat_value[0]

  @staticmethod
  def from_value(value):
    return TensorStructure(value.dtype, value.shape)

  def _to_legacy_output_types(self):
    return self._dtype

  def _to_legacy_output_shapes(self):
    return self._shape

  def _to_legacy_output_classes(self):
    return ops.Tensor

  def _batch(self, batch_size):
    return TensorStructure(
        self._dtype,
        tensor_shape.TensorShape([batch_size]).concatenate(self._shape))

  def _unbatch(self):
    if self._shape.ndims == 0:
      raise ValueError("Unbatching a tensor is only supported for rank >= 1")
    return TensorStructure(self._dtype, self._shape[1:])


@tf_export("data.experimental.SparseTensorStructure")
class SparseTensorStructure(Structure):
  """Represents structural information about a `tf.SparseTensor`."""

  def __init__(self, dtype, dense_shape):
    self._dtype = dtypes.as_dtype(dtype)
    self._dense_shape = tensor_shape.as_shape(dense_shape)

  def __eq__(self, other):
    return (isinstance(other, SparseTensorStructure) and tensor_spec.TensorSpec(
        self._dense_shape, self._dtype) == tensor_spec.TensorSpec(
            other._dense_shape, other._dtype))

  def __hash__(self):
    return hash(tensor_spec.TensorSpec(self._dense_shape, self._dtype))

  @property
  def _flat_shapes(self):
    # NOTE(mrry): The default flat shape of a boxed `SparseTensor` is `(3,)`,
    # but a `SparseTensorStructure` can also represent a batch of boxed
    # `SparseTensor` objects with shape `(?, 3)` (and batches of batches, etc.),
    # so the flat shape must be unknown.
    return [tensor_shape.unknown_shape(None)]

  @property
  def _flat_types(self):
    return [dtypes.variant]

  def is_compatible_with(self, other):
    return (isinstance(other, SparseTensorStructure) and
            self._dtype.is_compatible_with(other._dtype) and
            self._dense_shape.is_compatible_with(other._dense_shape))

  def _to_tensor_list(self, value):
    return [sparse_ops.serialize_sparse(value, out_type=dtypes.variant)]

  def _to_batched_tensor_list(self, value):
    if self._dense_shape.merge_with(
        tensor_util.constant_value_as_shape(value.dense_shape)).ndims == 0:
      raise ValueError(
          "Unbatching a sparse tensor is only supported for rank >= 1")
    return [sparse_ops.serialize_many_sparse(value, out_type=dtypes.variant)]

  def _from_tensor_list(self, flat_value):
    if (len(flat_value) != 1 or flat_value[0].dtype != dtypes.variant or
        not flat_value[0].shape.is_compatible_with(tensor_shape.vector(3))):
      raise ValueError("SparseTensorStructure corresponds to a single "
                       "tf.variant vector of length 3.")
    return self._from_compatible_tensor_list(flat_value)

  def _from_compatible_tensor_list(self, flat_value):
    ret = sparse_ops.deserialize_sparse(
        flat_value[0], dtype=self._dtype, rank=self._dense_shape.ndims)
    ret.indices.set_shape([None, self._dense_shape.ndims])
    ret.dense_shape.set_shape([self._dense_shape.ndims])
    return ret

  @staticmethod
  def from_value(value):
    sparse_tensor = sparse_tensor_lib.SparseTensor.from_value(value)
    return SparseTensorStructure(
        sparse_tensor.dtype,
        tensor_util.constant_value_as_shape(sparse_tensor.dense_shape))

  def _to_legacy_output_types(self):
    return self._dtype

  def _to_legacy_output_shapes(self):
    return self._dense_shape

  def _to_legacy_output_classes(self):
    return sparse_tensor_lib.SparseTensor

  def _batch(self, batch_size):
    return SparseTensorStructure(
        self._dtype,
        tensor_shape.TensorShape([batch_size]).concatenate(self._dense_shape))

  def _unbatch(self):
    if self._dense_shape.ndims == 0:
      raise ValueError("Unbatching a tensor is only supported for rank >= 1")
    return SparseTensorStructure(self._dtype, self._dense_shape[1:])


@tf_export("data.experimental.TensorArrayStructure")
class TensorArrayStructure(Structure):
  """Represents structural information about a `tf.TensorArray`."""

  def __init__(self, dtype, element_shape, dynamic_size, infer_shape):
    self._dtype = dtypes.as_dtype(dtype)
    self._element_shape = tensor_shape.as_shape(element_shape)
    self._dynamic_size = dynamic_size
    self._infer_shape = infer_shape

  def __eq__(self, other):
    return (isinstance(other, TensorArrayStructure) and tensor_spec.TensorSpec(
        self._element_shape, self._dtype) == tensor_spec.TensorSpec(
            other._element_shape, other._dtype) and
            self._dynamic_size == other._dynamic_size and
            self._infer_shape == other._infer_shape)

  def __hash__(self):
    return hash((tensor_spec.TensorSpec(self._element_shape, self._dtype),
                 self._dynamic_size, self._infer_shape))

  @property
  def _flat_shapes(self):
    # A TensorArray is represented via its variant object, which is a scalar.
    return [tensor_shape.scalar()]

  @property
  def _flat_types(self):
    return [dtypes.variant]

  def is_compatible_with(self, other):
    return (isinstance(other, TensorArrayStructure) and
            self._dtype.is_compatible_with(other._dtype) and
            self._element_shape.is_compatible_with(other._element_shape) and
            self._dynamic_size == other._dynamic_size)

  def _to_tensor_list(self, value):
    if not isinstance(value, tensor_array_ops.TensorArray):
      raise TypeError("value must be a TensorArray, but saw: {}"
                      .format(type(value)))
    if value.flow is not None and value.flow.dtype == dtypes.variant:
      return [value.flow]
    else:
      # Convert to a TF2-style TensorArray.
      # TODO(ebrevdo): Add an "_as_variant" method to TensorArray class, or
      # "implementation / as_variant" arg to TensorArray constructor.
      with ops.name_scope("convert_tensor_array"):
        flow = list_ops.tensor_list_from_tensor(
            tensor=value.stack(), element_shape=value.element_shape)
      return [flow]

  def _to_batched_tensor_list(self, value):
    raise NotImplementedError("TensorArrayStructure._to_batched_tensor_list")

  def _from_tensor_list(self, flat_value):
    if (len(flat_value) != 1 or flat_value[0].dtype != dtypes.variant or
        not flat_value[0].shape.is_compatible_with(tensor_shape.scalar())):
      raise ValueError("TensorArrayStructure corresponds to a single "
                       "tf.variant scalar.")
    return self._from_compatible_tensor_list(flat_value)

  def _from_compatible_tensor_list(self, flat_value):
    # This will return a TF2 Graph-style TensorArray because flat_value[0] is
    # a variant object.  size == -1 implies unknown size.
    ret = tensor_array_ops.TensorArray(
        dtype=self._dtype,
        flow=flat_value[0],
        dynamic_size=self._dynamic_size,
        infer_shape=self._infer_shape)
    ret._element_shape = [self._element_shape]
    return ret

  @staticmethod
  def from_value(value):
    if not isinstance(value, tensor_array_ops.TensorArray):
      raise TypeError("Expected value to be a TensorArray, but saw: {}".
                      format(type(value)))

    return TensorArrayStructure(
        dtype=value.dtype,
        element_shape=value.element_shape,
        dynamic_size=value.dynamic_size,
        infer_shape=value._infer_shape)

  def _to_legacy_output_types(self):
    return self._dtype

  def _to_legacy_output_shapes(self):
    # Sneak the dynamic_size and infer_shape values into the legacy shape.
    return (tensor_shape.matrix(self._dynamic_size, self._infer_shape)
            .concatenate(self._element_shape))

  def _to_legacy_output_classes(self):
    return tensor_array_ops.TensorArray

  def _batch(self, batch_size):
    raise NotImplementedError("TensorArrayStructure._batch")

  def _unbatch(self):
    raise NotImplementedError("TensorArrayStructure._unbatch")


@tf_export("data.experimental.RaggedTensorStructure")
class RaggedTensorStructure(Structure):
  """Represents structural information about a `tf.RaggedTensor`."""

  def __init__(self, dtype, shape, ragged_rank):
    self._dtype = dtypes.as_dtype(dtype)
    self._shape = tensor_shape.as_shape(shape)
    self._ragged_rank = ragged_rank

  def __eq__(self, other):
    return (isinstance(other, RaggedTensorStructure) and tensor_spec.TensorSpec(
        self._shape, self._dtype) == tensor_spec.TensorSpec(
            other._shape, other._dtype) and
            self._ragged_rank == other._ragged_rank)

  def __hash__(self):
    return hash((tensor_spec.TensorSpec(self._shape, self._dtype),
                 self._ragged_rank))

  @property
  def _flat_shapes(self):
    # A list of shapes matching the shapes of `self._to_tensor_list()`.
    # NOTE(mishragaurav): The default flat shape of a boxed `RaggedTensor` is
    # `[]` (scalar), but a `RaggedTensorStructure` can also represent a batch of
    # boxed `RaggedTensor` objects with shape `(?)` (and batches of batches,
    # etc.), so the flat shape must be unknown.
    return [tensor_shape.unknown_shape(None)]

  @property
  def _flat_types(self):
    return [dtypes.variant]

  def is_compatible_with(self, other):
    return (isinstance(other, RaggedTensorStructure) and
            self._dtype.is_compatible_with(other._dtype) and
            self._shape.is_compatible_with(other._shape) and
            self._ragged_rank == other._ragged_rank)

  def _to_tensor_list(self, value):
    return [value._to_variant()]

  def _to_batched_tensor_list(self, value):
    return [value._to_variant(batched_input=True)]

  def _from_tensor_list(self, flat_value):
    if (len(flat_value) != 1 or flat_value[0].dtype != dtypes.variant):
      raise ValueError("RaggedTensorStructure corresponds to a single "
                       "tf.variant scalar.")
    return self._from_compatible_tensor_list(flat_value)

  def _from_compatible_tensor_list(self, flat_value):
    if self._ragged_rank <= 0:
      raise ValueError(
          "ragged_rank must be greater than zero. Found ragged_rank: %d" %
          self._ragged_rank)
    return ragged_tensor.RaggedTensor._from_variant(
        flat_value[0], dtype=self._dtype, output_ragged_rank=self._ragged_rank)

  @staticmethod
  def from_value(value):
    return RaggedTensorStructure(value.dtype, value.shape, value.ragged_rank)

  def _to_legacy_output_types(self):
    return self._dtype

  def _to_legacy_output_shapes(self):
    return self._shape

  def _to_legacy_output_classes(self):
    return self

  def _batch(self, batch_size):
    return RaggedTensorStructure(
        self._dtype,
        tensor_shape.TensorShape([batch_size]).concatenate(self._shape),
        self._ragged_rank + 1)

  def _unbatch(self):
    # Note: Any ragged_rank is allowed here because the dataset could be
    # subsequently batched again. Errors are handled in
    # RaggedTensorStructure._from_compatible_tensor_list()
    return RaggedTensorStructure(self._dtype, self._shape[1:],
                                 self._ragged_rank - 1)
