# Copyright 2015 The TensorFlow Authors. All Rights Reserved.
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
# pylint: disable=protected-access
"""Contains the base Layer class, from which all layers inherit."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import functools
import inspect  # Necessary supplement to tf_inspect to deal with variadic args.
import itertools

import numpy as np
from six.moves import zip  # pylint: disable=redefined-builtin

from tensorflow.core.framework import node_def_pb2
from tensorflow.python import autograph
from tensorflow.python.distribute import distribution_strategy_context as ds_context
from tensorflow.python.distribute import values as distribute_values
from tensorflow.python.eager import context
from tensorflow.python.eager import execute
from tensorflow.python.eager import function
from tensorflow.python.framework import auto_control_deps
from tensorflow.python.framework import dtypes
from tensorflow.python.framework import func_graph
from tensorflow.python.framework import ops
from tensorflow.python.framework import tensor_util
from tensorflow.python.keras import backend
from tensorflow.python.keras import constraints
from tensorflow.python.keras import initializers
from tensorflow.python.keras import regularizers
from tensorflow.python.keras.engine import base_layer_utils
from tensorflow.python.keras.engine import input_spec
from tensorflow.python.keras.mixed_precision.experimental import autocast_variable
from tensorflow.python.keras.mixed_precision.experimental import policy
from tensorflow.python.keras.utils import generic_utils
from tensorflow.python.keras.utils import tf_utils
# A module that only depends on `keras.layers` import these from here.
from tensorflow.python.keras.utils.generic_utils import to_snake_case  # pylint: disable=unused-import
from tensorflow.python.keras.utils.tf_utils import is_tensor_or_tensor_list  # pylint: disable=unused-import
from tensorflow.python.module import module
from tensorflow.python.ops import array_ops
from tensorflow.python.ops import math_ops
from tensorflow.python.ops import resource_variable_ops
from tensorflow.python.ops import variables as tf_variables
from tensorflow.python.training.tracking import base as trackable
from tensorflow.python.training.tracking import data_structures
from tensorflow.python.training.tracking import layer_utils as trackable_layer_utils
from tensorflow.python.training.tracking import object_identity
from tensorflow.python.training.tracking import tracking
from tensorflow.python.util import compat
from tensorflow.python.util import function_utils
from tensorflow.python.util import nest
from tensorflow.python.util import tf_decorator
from tensorflow.python.util import tf_inspect
from tensorflow.python.util.tf_export import keras_export
from tensorflow.tools.docs import doc_controls

# Prefix that is added to the TF op layer names.
_TF_OP_LAYER_NAME_PREFIX = 'tf_op_layer_'


@keras_export('keras.layers.Layer')
class Layer(module.Module):
  """Base layer class.

  This is the class from which all layers inherit.

  A layer is a class implementing common neural networks operations, such
  as convolution, batch norm, etc. These operations require managing weights,
  losses, updates, and inter-layer connectivity.

  Users will just instantiate a layer and then treat it as a callable.

  We recommend that descendants of `Layer` implement the following methods:

  * `__init__()`: Save configuration in member variables
  * `build()`: Called once from `__call__`, when we know the shapes of inputs
    and `dtype`. Should have the calls to `add_weight()`, and then
    call the super's `build()` (which sets `self.built = True`, which is
    nice in case the user wants to call `build()` manually before the
    first `__call__`).
  * `call()`: Called in `__call__` after making sure `build()` has been called
    once. Should actually perform the logic of applying the layer to the
    input tensors (which should be passed in as the first argument).

  Arguments:
    trainable: Boolean, whether the layer's variables should be trainable.
    name: String name of the layer.
    dtype: Default dtype of the layer's weights (default of `None` means use the
      type of the first input).
    dynamic: Set this to `True` if your layer should only be run eagerly, and
      should not be used to generate a static computation graph.
      This would be the case for a Tree-RNN or a recursive network,
      for example, or generally for any layer that manipulates tensors
      using Python control flow. If `False`, we assume that the layer can
      safely be used to generate a static computation graph.

  Read-only properties:
    name: The name of the layer (string).
    dtype: Default dtype of the layer's weights (default of `None` means use the
      type of the first input).
    updates: List of update ops of this layer.
    losses: List of losses added by this layer.
    trainable_weights: List of variables to be included in backprop.
    non_trainable_weights: List of variables that should not be
      included in backprop.
    weights: The concatenation of the lists trainable_weights and
      non_trainable_weights (in this order).

  Mutable properties:
    trainable: Whether the layer should be trained (boolean).
    input_spec: Optional (list of) `InputSpec` object(s) specifying the
      constraints on inputs that can be accepted by the layer.
  """

  # See tf.Module for the usage of this property.
  # The key for _obj_reference_counts_dict is a Trackable, which could be a
  # variable or layer etc. tf.Module._flatten will fail to flatten the key
  # since it is trying to convert Trackable to a string. This attribute can be
  # ignored even after the fix of nest lib, since the trackable object should
  # already been available as individual attributes. _obj_reference_counts_dict
  # just contains a copy of them.
  _TF_MODULE_IGNORED_PROPERTIES = frozenset(itertools.chain(
      ('_obj_reference_counts_dict',),
      module.Module._TF_MODULE_IGNORED_PROPERTIES
  ))

  @trackable.no_automatic_dependency_tracking
  def __init__(self, trainable=True, name=None, dtype=None, dynamic=False,
               **kwargs):
    # These properties should be set by the user via keyword arguments.
    # note that 'dtype', 'input_shape' and 'batch_input_shape'
    # are only applicable to input layers: do not pass these keywords
    # to non-input layers.
    allowed_kwargs = {
        'input_shape',
        'batch_input_shape',
        'batch_size',
        'weights',
        'activity_regularizer',
    }
    # Validate optional keyword arguments.
    generic_utils.validate_kwargs(kwargs, allowed_kwargs)

    # Mutable properties
    # Indicates whether the layer's weights are updated during training
    # and whether the layer's updates are run during training.
    self._trainable = trainable
    # A stateful layer is a layer whose updates are run during inference too,
    # for instance stateful RNNs.
    self.stateful = False
    # Indicates whether `build` needs to be called upon layer call, to create
    # the layer's weights.
    self.built = False
    # Provides information about which inputs are compatible with the layer.
    self.input_spec = None
    self.supports_masking = False

    self._init_set_name(name)
    self._activity_regularizer = kwargs.pop('activity_regularizer', None)
    self._maybe_create_attribute('_trainable_weights', [])
    self._maybe_create_attribute('_non_trainable_weights', [])
    self._updates = []
    # A list of zero-argument lambdas which return Tensors, used for variable
    # regularizers.
    self._callable_losses = []
    # A list of symbolic Tensors containing activity regularizers and losses
    # manually added through `add_loss` in graph-building mode.
    self._losses = []
    # A list of loss values containing activity regularizers and losses
    # manually added through `add_loss` during eager execution. It is cleared
    # after every batch.
    # Because we plan on eventually allowing a same model instance to be trained
    # in eager mode or graph mode alternatively, we need to keep track of
    # eager losses and symbolic losses via separate attributes.
    self._eager_losses = []
    # A list of metric instances corresponding to the symbolic metric tensors
    # added using the `add_metric` API.
    self._metrics = []
    # TODO(psv): Remove this property.
    # A dictionary that maps metric names to metric result tensors. The results
    # are the running averages of metric values over an epoch.
    self._metrics_tensors = {}

    self._set_dtype_and_policy(dtype)
    self._call_convention = (base_layer_utils
                             .CallConvention.EXPLICIT_INPUTS_ARGUMENT)
    # Dependencies tracked via attribute assignment.
    self._maybe_create_attribute('_layers', [])

    # These lists will be filled via successive calls
    # to self._add_inbound_node().
    self._inbound_nodes = []
    self._outbound_nodes = []

    call_argspec = tf_inspect.getfullargspec(self.call)
    self._expects_training_arg = 'training' in call_argspec.args

    # Whether the `call` method can be used to build a TF graph without issues.
    self._dynamic = dynamic

    # Manage input shape information if passed.
    if 'input_shape' in kwargs or 'batch_input_shape' in kwargs:
      # In this case we will later create an input layer
      # to insert before the current layer
      if 'batch_input_shape' in kwargs:
        batch_input_shape = tuple(kwargs['batch_input_shape'])
      elif 'input_shape' in kwargs:
        if 'batch_size' in kwargs:
          batch_size = kwargs['batch_size']
        else:
          batch_size = None
        batch_input_shape = (batch_size,) + tuple(kwargs['input_shape'])
      self._batch_input_shape = batch_input_shape

    # Manage initial weight values if passed.
    if 'weights' in kwargs:
      self._initial_weights = kwargs['weights']
    else:
      self._initial_weights = None

  def build(self, input_shape):
    """Creates the variables of the layer (optional, for subclass implementers).

    This is a method that implementers of subclasses of `Layer` or `Model`
    can override if they need a state-creation step in-between
    layer instantiation and layer call.

    This is typically used to create the weights of `Layer` subclasses.

    Arguments:
      input_shape: Instance of `TensorShape`, or list of instances of
        `TensorShape` if the layer expects a list of inputs
        (one instance per input).
    """
    self.built = True

  @doc_controls.for_subclass_implementers
  def call(self, inputs, **kwargs):  # pylint: disable=unused-argument
    """This is where the layer's logic lives.

    Arguments:
        inputs: Input tensor, or list/tuple of input tensors.
        **kwargs: Additional keyword arguments.

    Returns:
        A tensor or list/tuple of tensors.
    """
    return inputs

  @doc_controls.for_subclass_implementers
  def add_weight(self,
                 name=None,
                 shape=None,
                 dtype=None,
                 initializer=None,
                 regularizer=None,
                 trainable=None,
                 constraint=None,
                 partitioner=None,
                 use_resource=None,
                 synchronization=tf_variables.VariableSynchronization.AUTO,
                 aggregation=tf_variables.VariableAggregation.NONE,
                 **kwargs):
    """Adds a new variable to the layer.

    Arguments:
      name: Variable name.
      shape: Variable shape. Defaults to scalar if unspecified.
      dtype: The type of the variable. Defaults to `self.dtype` or `float32`.
      initializer: initializer instance (callable).
      regularizer: regularizer instance (callable).
      trainable: whether the variable should be part of the layer's
        "trainable_variables" (e.g. variables, biases)
        or "non_trainable_variables" (e.g. BatchNorm mean, stddev).
        Note, if the current variable scope is marked as non-trainable
        then this parameter is ignored and any added variables are also
        marked as non-trainable. `trainable` defaults to `True` unless
        `synchronization` is set to `ON_READ`.
      constraint: constraint instance (callable).
      partitioner: Partitioner to be passed to the `Trackable` API.
      use_resource: Whether to use `ResourceVariable`.
      synchronization: Indicates when a distributed a variable will be
        aggregated. Accepted values are constants defined in the class
        `tf.VariableSynchronization`. By default the synchronization is set to
        `AUTO` and the current `DistributionStrategy` chooses
        when to synchronize. If `synchronization` is set to `ON_READ`,
        `trainable` must not be set to `True`.
      aggregation: Indicates how a distributed variable will be aggregated.
        Accepted values are constants defined in the class
        `tf.VariableAggregation`.
      **kwargs: Additional keyword arguments. Accepted values are `getter` and
        `collections`.

    Returns:
      The created variable.  Usually either a `Variable` or `ResourceVariable`
      instance.  If `partitioner` is not `None`, a `PartitionedVariable`
      instance is returned.

    Raises:
      RuntimeError: If called with partioned variable regularization and
        eager execution is enabled.
      ValueError: When giving unsupported dtype and no initializer or when
        trainable has been set to True with synchronization set as `ON_READ`.
    """
    if shape is None:
      shape = ()
    # Validate optional keyword arguments.
    for kwarg in kwargs:
      if kwarg not in ['getter', 'collections', 'experimental_autocast']:
        raise TypeError('Unknown keyword argument:', kwarg)
    getter = kwargs.pop('getter', None)
    collections_arg = kwargs.pop('collections', None)
    # 'experimental_autocast' can be set to False by the caller to indicate an
    # AutoCastVariable should never be created.
    autocast = kwargs.pop('experimental_autocast', True)

    if dtype is None:
      dtype = self.dtype or backend.floatx()
    dtype = dtypes.as_dtype(dtype)
    if self._dtype is None:
      self._dtype = dtype.base_dtype.name
    initializer = initializers.get(initializer)
    regularizer = regularizers.get(regularizer)
    constraint = constraints.get(constraint)

    if synchronization == tf_variables.VariableSynchronization.ON_READ:
      if trainable:
        raise ValueError(
            'Synchronization value can be set to '
            'VariableSynchronization.ON_READ only for non-trainable variables. '
            'You have specified trainable=True and '
            'synchronization=VariableSynchronization.ON_READ.')
      else:
        # Set trainable to be false when variable is to be synced on read.
        trainable = False
    elif trainable is None:
      trainable = True

    # Initialize variable when no initializer provided
    if initializer is None:
      # If dtype is DT_FLOAT, provide a uniform unit scaling initializer
      if dtype.is_floating:
        initializer = initializers.glorot_uniform()
      # If dtype is DT_INT/DT_UINT, provide a default value `zero`
      # If dtype is DT_BOOL, provide a default value `FALSE`
      elif dtype.is_integer or dtype.is_unsigned or dtype.is_bool:
        initializer = initializers.zeros()
      # NOTES:Do we need to support for handling DT_STRING and DT_COMPLEX here?
      else:
        raise ValueError('An initializer for variable %s of type %s is required'
                         ' for layer %s' % (name, dtype.base_dtype, self.name))

    variable = self._add_variable_with_custom_getter(
        name=name,
        shape=shape,
        # TODO(allenl): a `make_variable` equivalent should be added as a
        # `Trackable` method.
        getter=getter or base_layer_utils.make_variable,
        # Manage errors in Layer rather than Trackable.
        overwrite=True,
        initializer=initializer,
        dtype=dtype,
        constraint=constraint,
        trainable=trainable,
        partitioner=partitioner,
        use_resource=use_resource,
        collections=collections_arg,
        synchronization=synchronization,
        aggregation=aggregation)
    backend.track_variable(variable)

    if autocast and self._mixed_precision_policy.should_cast_variables:
      if isinstance(variable, distribute_values.DistributedVariable):
        variable = autocast_variable.AutoCastDistributedVariable(variable)
      else:
        variable = autocast_variable.AutoCastVariable(variable)

    if regularizer is not None:
      # TODO(fchollet): in the future, this should be handled at the
      # level of variable creation, and weight regularization losses
      # should be variable attributes.
      name_in_scope = variable.name[:variable.name.find(':')]
      self._handle_weight_regularization(name_in_scope,
                                         variable,
                                         regularizer)
    if trainable:
      self._trainable_weights.append(variable)
    else:
      self._non_trainable_weights.append(variable)
    return variable

  def get_config(self):
    """Returns the config of the layer.

    A layer config is a Python dictionary (serializable)
    containing the configuration of a layer.
    The same layer can be reinstantiated later
    (without its trained weights) from this configuration.

    The config of a layer does not include connectivity
    information, nor the layer class name. These are handled
    by `Network` (one layer of abstraction above).

    Returns:
        Python dictionary.
    """
    config = {'name': self.name, 'trainable': self.trainable}
    if hasattr(self, '_batch_input_shape'):
      config['batch_input_shape'] = self._batch_input_shape
    if hasattr(self, 'dtype'):
      config['dtype'] = self.dtype
    # TODO(reedwm): Handle serializing self._mixed_precision_policy.
    return config

  @classmethod
  def from_config(cls, config):
    """Creates a layer from its config.

    This method is the reverse of `get_config`,
    capable of instantiating the same layer from the config
    dictionary. It does not handle layer connectivity
    (handled by Network), nor weights (handled by `set_weights`).

    Arguments:
        config: A Python dictionary, typically the
            output of get_config.

    Returns:
        A layer instance.
    """
    return cls(**config)

  def compute_output_shape(self, input_shape):
    """Computes the output shape of the layer.

    Assumes that the layer will be built
    to match that input shape provided.

    Arguments:
        input_shape: Shape tuple (tuple of integers)
            or list of shape tuples (one per output tensor of the layer).
            Shape tuples can include None for free dimensions,
            instead of an integer.

    Returns:
        An input shape tuple.
    """
    if context.executing_eagerly():
      # In this case we build the model first in order to do shape inference.
      # This is acceptable because the framework only calls
      # `compute_output_shape` on shape values that the layer would later be
      # built for. It would however cause issues in case a user attempts to
      # use `compute_output_shape` manually (these users will have to
      # implement `compute_output_shape` themselves).
      self.build(input_shape)
      with context.graph_mode():
        graph = func_graph.FuncGraph('graph')
        with graph.as_default():
          input_shape = tf_utils.convert_shapes(input_shape, to_tuples=False)
          inputs = nest.map_structure(
              base_layer_utils.generate_placeholders_from_shape, input_shape)
          try:
            if self._expects_training_arg:
              outputs = self(inputs, training=False)
            else:
              outputs = self(inputs)
          except TypeError:
            raise NotImplementedError('We could not automatically infer '
                                      'the static shape of the layer\'s output.'
                                      ' Please implement the '
                                      '`compute_output_shape` method on your '
                                      'layer (%s).' % self.__class__.__name__)
      return nest.map_structure(lambda t: t.shape, outputs)
    raise NotImplementedError

  def compute_mask(self, inputs, mask=None):  # pylint: disable=unused-argument
    """Computes an output mask tensor.

    Arguments:
        inputs: Tensor or list of tensors.
        mask: Tensor or list of tensors.

    Returns:
        None or a tensor (or list of tensors,
            one per output tensor of the layer).
    """
    if not self.supports_masking:
      if any(m is not None for m in nest.flatten(mask)):
        raise TypeError('Layer ' + self.name + ' does not support masking, '
                        'but was passed an input_mask: ' + str(mask))
      # masking not explicitly supported: return None as mask.
      return None
    # if masking is explicitly supported, by default
    # carry over the input mask
    return mask

  def __call__(self, inputs, *args, **kwargs):
    """Wraps `call`, applying pre- and post-processing steps.

    Arguments:
      inputs: input tensor(s).
      *args: additional positional arguments to be passed to `self.call`.
      **kwargs: additional keyword arguments to be passed to `self.call`.

    Returns:
      Output tensor(s).

    Note:
      - The following optional keyword arguments are reserved for specific uses:
        * `training`: Boolean scalar tensor of Python boolean indicating
          whether the `call` is meant for training or inference.
        * `mask`: Boolean input mask.
      - If the layer's `call` method takes a `mask` argument (as some Keras
        layers do), its default value will be set to the mask generated
        for `inputs` by the previous layer (if `input` did come from
        a layer that generated a corresponding mask, i.e. if it came from
        a Keras layer with masking support.

    Raises:
      ValueError: if the layer's `call` method returns None (an invalid value).
    """
    input_list = nest.flatten(inputs)
    # Accept NumPy inputs by converting to Tensors.
    if any(isinstance(x, (np.ndarray, float, int)) for x in input_list):
      # Don't call `ops.convert_to_tensor` on all `inputs` because
      # `SparseTensors` can't be converted to `Tensor`.
      def _convert_non_tensor(x):
        if isinstance(x, (np.ndarray, float, int)):
          return ops.convert_to_tensor(x)
        return x

      inputs = nest.map_structure(_convert_non_tensor, inputs)
      input_list = nest.flatten(inputs)

    # We will attempt to build a TF graph if & only if all inputs are symbolic.
    # This is always the case in graph mode. It can also be the case in eager
    # mode when all inputs can be traced back to `keras.Input()` (when building
    # models using the functional API).
    build_graph = tf_utils.are_all_symbolic_tensors(input_list)

    if build_graph:
      # Only create Keras history if at least one tensor originates from a
      # `keras.Input`. Otherwise this Layer may be being used outside the Keras
      # framework.
      if base_layer_utils.needs_keras_history(inputs):
        base_layer_utils.create_keras_history(inputs)

    # Handle Keras mask propagation from previous layer to current layer.
    previous_mask = None
    if self._should_compute_mask:
      previous_mask = base_layer_utils.collect_previous_mask(inputs)
      if ('mask' in self._call_fn_args and 'mask' not in kwargs and
          not generic_utils.is_all_none(previous_mask)):
        # The previous layer generated a mask, and mask was not explicitly
        # pass to __call__, hence we set previous_mask as the default value.
        kwargs['mask'] = previous_mask

    # Clear eager losses on top level model call.
    # We are clearing the losses only on the top level model call and not on
    # every layer/mode call because layer/model may be reused.
    if (base_layer_utils.is_in_eager_or_tf_function() and
        not base_layer_utils.is_in_call_context()):
      self._clear_losses()

    with base_layer_utils.call_context(self, build_graph):
      # Check input assumptions set after layer building, e.g. input shape.
      if build_graph:
        # Symbolic execution on symbolic tensors. We will attempt to build
        # the corresponding TF subgraph inside `backend.get_graph()`
        input_spec.assert_input_compatibility(self.input_spec, inputs,
                                              self.name)
        graph = backend.get_graph()
        with graph.as_default(), backend.name_scope(self._name_scope()):
          # Build layer if applicable (if the `build` method has been
          # overridden).
          self._maybe_build(inputs)

          # Wrapping `call` function in autograph to allow for dynamic control
          # dependencies in call. We are limiting this to subclassed layers as
          # autograph is strictly needed only for subclassed layers.
          if base_layer_utils.is_subclassed(self):
            decorators, original_func = tf_decorator.unwrap(self.call)
            converted_func = autograph.convert(recursive=True)(original_func)
            if decorators:
              call_fn = tf_decorator.rewrap(self.call, original_func,
                                            converted_func)
            else:
              call_fn = converted_func
          else:
            call_fn = self.call

          # Explicitly pass the learning phase placeholder to `call` if
          # the `training` argument was left unspecified by the user.
          # This behavior is restricted to the managed Keras FuncGraph.
          # TODO(omalleyt): Reconcile this with new `trainable` behavior
          # when available.
          learning_phase_passed_by_framework = False
          if (self._expects_training_arg and
              not base_layer_utils.training_arg_passed_to_call(
                  tf_inspect.getfullargspec(self.call), args, kwargs) and
              base_layer_utils.is_in_keras_graph()):
            learning_phase_passed_by_framework = True
            kwargs['training'] = backend.learning_phase()
          if not self.dynamic:
            try:
              with base_layer_utils.autocast_context_manager(
                  input_list,
                  self._mixed_precision_policy.should_cast_variables):
                # Add auto_control_deps in V2 when they are not already added by
                # a `tf.function`.
                if (ops.executing_eagerly_outside_functions() and
                    not base_layer_utils.is_in_eager_or_tf_function()):
                  with auto_control_deps.AutomaticControlDependencies() as acd:
                    outputs = call_fn(inputs, *args, **kwargs)
                    # Wrap Tensors in `outputs` in `tf.identity` to avoid
                    # circular dependencies.
                    outputs = base_layer_utils.mark_as_return(outputs, acd)
                else:
                  outputs = call_fn(inputs, *args, **kwargs)

            except TypeError as e:
              exception_str = str(e)
              exception_msg = 'Tensor objects are only iterable when eager'
              if exception_msg in exception_str:
                raise TypeError('You are attempting to use Python control '
                                'flow in a layer that was not declared to be '
                                'dynamic. Pass `dynamic=True` to the class '
                                'constructor.\nEncountered error:\n"""\n' +
                                exception_str + '\n"""')
              raise
          else:
            # We will use static shape inference to return symbolic tensors
            # matching the specifications of the layer outputs.
            # Since `self.dynamic` is True, we will never attempt to
            # run the underlying TF graph (which is disconnected).
            # TODO(fchollet): consider py_func as an alternative, which
            # would enable us to run the underlying graph if needed.
            outputs = self._symbolic_call(inputs)

          if outputs is None:
            raise ValueError('A layer\'s `call` method should return a '
                             'Tensor or a list of Tensors, not None '
                             '(layer: ' + self.name + ').')
          if base_layer_utils.have_all_keras_metadata(inputs):
            if learning_phase_passed_by_framework:
              kwargs.pop('training')
            inputs, outputs = self._set_connectivity_metadata_(
                inputs, outputs, args, kwargs)
          self._handle_activity_regularization(inputs, outputs)
          self._set_mask_metadata(inputs, outputs, previous_mask)
          if hasattr(self, '_set_inputs') and not self.inputs:
            # Subclassed network: explicitly set metadata normally set by
            # a call to self._set_inputs().
            # TODO(b/120997007): This should be done in Eager as well, but
            # causes garbage collection issues because of the placeholders
            # created on the default Keras graph.
            self._set_inputs(inputs, outputs)
      else:
        # Eager execution on data tensors.
        with backend.name_scope(self._name_scope()):
          self._maybe_build(inputs)
          with base_layer_utils.autocast_context_manager(
              input_list, self._mixed_precision_policy.should_cast_variables):
            outputs = self.call(inputs, *args, **kwargs)
          self._handle_activity_regularization(inputs, outputs)
          self._set_mask_metadata(inputs, outputs, previous_mask)

    if not context.executing_eagerly():
      # Optionally load weight values specified at layer instantiation.
      # TODO(fchollet): consider enabling this with eager execution too.
      if (hasattr(self, '_initial_weights') and
          self._initial_weights is not None):
        self.set_weights(self._initial_weights)
        del self._initial_weights

    return outputs

  @property
  def dtype(self):
    return self._dtype

  @property
  def name(self):
    return self._name

  @property
  def dynamic(self):
    return self._dynamic

  @property
  def trainable(self):
    return self._trainable

  @trainable.setter
  def trainable(self, value):
    self._trainable = value
    for layer in getattr(self, '_layers', []):
      layer.trainable = value

  @property
  def activity_regularizer(self):
    """Optional regularizer function for the output of this layer."""
    return self._activity_regularizer

  @activity_regularizer.setter
  def activity_regularizer(self, regularizer):
    """Optional regularizer function for the output of this layer."""
    self._activity_regularizer = regularizer

  @property
  def trainable_weights(self):
    if self.trainable:
      nested = self._gather_children_attribute('trainable_weights')
      return self._trainable_weights + nested
    else:
      return []

  @property
  def non_trainable_weights(self):
    if self.trainable:
      nested = self._gather_children_attribute('non_trainable_weights')
      return self._non_trainable_weights + nested
    else:
      nested = self._gather_children_attribute('weights')
      return self._trainable_weights + self._non_trainable_weights + nested

  @property
  def weights(self):
    """Returns the list of all layer variables/weights.

    Returns:
      A list of variables.
    """
    return self.trainable_weights + self.non_trainable_weights

  @property
  def updates(self):
    if not self.trainable and not self.stateful:
      return []
    with backend.get_graph().as_default():
      updates = []
      for u in self._updates:
        # Filter out updates created in a cross-replica context when in a
        # replica context and vice versa.
        if (getattr(u, '_in_cross_replica_context', False) !=
            ds_context.in_cross_replica_context()):
          continue
        if callable(u):
          try:
            u = u()
          except ValueError as e:
            if 'Trying to capture a tensor from an inner function' in str(e):
              base_layer_utils.check_graph_consistency(
                  method='add_update', force_raise=True)
            raise
        base_layer_utils.check_graph_consistency(u, method='add_update')
        updates.append(u)
    return updates + self._gather_children_attribute('updates')

  @property
  def losses(self):
    """Losses which are associated with this `Layer`.

    Variable regularization tensors are created when this property is accessed,
    so it is eager safe: accessing `losses` under a `tf.GradientTape` will
    propagate gradients back to the corresponding variables.

    Returns:
      A list of tensors.
    """
    collected_losses = []

    # If any eager losses are present, we assume the model to be part of an
    # eager training loop (either a custom one or the one used when
    # `run_eagerly=True`), and so we always return just the eager losses in that
    # case.
    if self._eager_losses:
      collected_losses.extend(self._eager_losses)
    else:
      collected_losses.extend(self._losses)
    for regularizer in self._callable_losses:
      loss_tensor = regularizer()
      if loss_tensor is not None:
        collected_losses.append(loss_tensor)
    return collected_losses + self._gather_children_attribute('losses')

  @doc_controls.for_subclass_implementers
  def add_loss(self, losses, inputs=None):
    """Add loss tensor(s), potentially dependent on layer inputs.

    Some losses (for instance, activity regularization losses) may be dependent
    on the inputs passed when calling a layer. Hence, when reusing the same
    layer on different inputs `a` and `b`, some entries in `layer.losses` may
    be dependent on `a` and some on `b`. This method automatically keeps track
    of dependencies.

    This method can be used inside a subclassed layer or model's `call`
    function, in which case `losses` should be a Tensor or list of Tensors.

    Example:

    ```python
    class MyLayer(tf.keras.layers.Layer):
      def call(inputs, self):
        self.add_loss(tf.abs(tf.reduce_mean(inputs)), inputs=True)
        return inputs
    ```

    This method can also be called directly on a Functional Model during
    construction. In this case, any loss Tensors passed to this Model must
    be symbolic and be able to be traced back to the model's `Input`s. These
    losses become part of the model's topology and are tracked in `get_config`.

    Example:

    ```python
    inputs = tf.keras.Input(shape=(10,))
    x = tf.keras.layers.Dense(10)(inputs)
    outputs = tf.keras.layers.Dense(1)(x)
    model = tf.keras.Model(inputs, outputs)
    # Actvity regularization.
    model.add_loss(tf.abs(tf.reduce_mean(x)))
    ```

    If this is not the case for your loss (if, for example, your loss references
    a `Variable` of one of the model's layers), you can wrap your loss in a
    zero-argument lambda. These losses are not tracked as part of the model's
    topology since they can't be serialized.

    Example:

    ```python
    inputs = tf.keras.Input(shape=(10,))
    x = tf.keras.layers.Dense(10)(inputs)
    outputs = tf.keras.layers.Dense(1)(x)
    model = tf.keras.Model(inputs, outputs)
    # Weight regularization.
    model.add_loss(lambda: tf.reduce_mean(x.kernel))
    ```

    The `get_losses_for` method allows to retrieve the losses relevant to a
    specific set of inputs.

    Arguments:
      losses: Loss tensor, or list/tuple of tensors. Rather than tensors, losses
        may also be zero-argument callables which create a loss tensor.
      inputs: Ignored when executing eagerly. If anything other than None is
        passed, it signals the losses are conditional on some of the layer's
        inputs, and thus they should only be run where these inputs are
        available. This is the case for activity regularization losses, for
        instance. If `None` is passed, the losses are assumed
        to be unconditional, and will apply across all dataflows of the layer
        (e.g. weight regularization losses).
    """
    def _tag_unconditional(loss):
      if callable(loss):
        loss = loss()
      if loss is None:
        return None  # Will be filtered out when computing the .losses property
      if not tensor_util.is_tensor(loss):
        loss = ops.convert_to_tensor(loss, dtype=backend.floatx())
      base_layer_utils.check_graph_consistency(loss, method='add_loss')
      loss._unconditional_loss = (inputs is None)  # pylint: disable=protected-access
      return loss

    losses = nest.flatten(losses)

    callable_losses = []
    eager_losses = []
    symbolic_losses = []
    for loss in losses:
      if callable(loss):
        callable_losses.append(functools.partial(_tag_unconditional, loss))
        continue
      if loss is None:
        continue
      if not tensor_util.is_tensor(loss):
        loss = ops.convert_to_tensor(loss, dtype=backend.floatx())
      # TF Functions should take the eager path.
      if (tf_utils.is_symbolic_tensor(loss) and
          not base_layer_utils.is_in_tf_function()):
        symbolic_losses.append(_tag_unconditional(loss))
      elif tensor_util.is_tensor(loss):
        eager_losses.append(_tag_unconditional(loss))

    self._callable_losses += callable_losses

    call_context = base_layer_utils.is_in_call_context()
    if eager_losses and not call_context:
      raise ValueError(
          'Expected a symbolic Tensors or a callable for the loss value. '
          'Please wrap your loss computation in a zero argument `lambda`.')

    self._eager_losses += eager_losses

    if call_context:
      for symbolic_loss in symbolic_losses:
        self._losses.append(symbolic_loss)
    else:
      for symbolic_loss in symbolic_losses:
        if getattr(self, '_is_graph_network', False):
          new_layers = base_layer_utils.create_keras_history(symbolic_loss)
          # Losses must be keyed on inputs no matter what in order to
          # be supported in DistributionStrategy.
          add_loss_layer = AddLoss(unconditional=False)
          add_loss_layer(symbolic_loss)
          new_layers.append(add_loss_layer)
          self._insert_layers(new_layers)
        else:
          # Possible a loss was added in a Layer's `build`.
          self._losses.append(symbolic_loss)

  @trackable.no_automatic_dependency_tracking
  def _clear_losses(self):
    """Used every step in eager to reset losses."""
    self._eager_losses = []
    if hasattr(self, '_layers'):
      for layer in trackable_layer_utils.filter_empty_layer_containers(
          self._layers):
        layer._clear_losses()

  @property
  def metrics(self):
    return self._metrics + self._gather_children_attribute('metrics')

  @doc_controls.for_subclass_implementers
  def add_metric(self, value, aggregation=None, name=None):
    """Adds metric tensor to the layer.

    Args:
      value: Metric tensor.
      aggregation: Sample-wise metric reduction function. If `aggregation=None`,
        it indicates that the metric tensor provided has been aggregated
        already. eg, `bin_acc = BinaryAccuracy(name='acc')` followed by
        `model.add_metric(bin_acc(y_true, y_pred))`. If aggregation='mean', the
        given metric tensor will be sample-wise reduced using `mean` function.
        eg, `model.add_metric(tf.reduce_sum(outputs), name='output_mean',
        aggregation='mean')`.
      name: String metric name.

    Raises:
      ValueError: If `aggregation` is anything other than None or `mean`.
    """
    if aggregation is not None and aggregation != 'mean':
      raise ValueError(
          'We currently support only `mean` sample-wise metric aggregation. '
          'You provided aggregation=`%s`' % aggregation)

    from_metric_obj = hasattr(value, '_metric_obj')
    is_symbolic = tf_utils.is_symbolic_tensor(value)
    call_context = base_layer_utils.is_in_call_context()

    if name is None and not from_metric_obj:
      # Eg. `self.add_metric(math_ops.reduce_sum(x), aggregation='mean')`
      # In eager mode, we use metric name to lookup a metric. Without a name,
      # a new Mean metric wrapper will be created on every model/layer call.
      # So, we raise an error when no name is provided.
      # We will do the same for symbolic mode for consistency although a name
      # will be generated if no name is provided.

      # We will not raise this error in the foll use case for the sake of
      # consistency as name in provided in the metric constructor.
      # mean = metrics.Mean(name='my_metric')
      # model.add_metric(mean(outputs))
      raise ValueError('Please provide a name for your metric like '
                       '`self.add_metric(tf.reduce_sum(inputs), '
                       'name=\'mean_activation\', aggregation=\'mean\')`')
    elif from_metric_obj:
      name = value._metric_obj.name

    if call_context:
      # TF Function path should take the eager path.
      if is_symbolic and not base_layer_utils.is_in_tf_function():
        self._symbolic_add_metric(value, aggregation, name)
      else:
        self._eager_add_metric(value, aggregation, name)
    else:
      if not is_symbolic:
        raise ValueError('Expected a symbolic Tensor for the metric value, '
                         'received: ' + str(value))

      # Possible a metric was added in a Layer's `build`.
      if not getattr(self, '_is_graph_network', False):
        with backend.get_graph().as_default():
          self._symbolic_add_metric(value, aggregation, name)
        return

      if from_metric_obj:
        raise ValueError('Using the result of calling a `Metric` object '
                         'when calling `add_metric` on a Functional '
                         'Model is not supported. Please pass the '
                         'Tensor to monitor directly.')

      # Insert layers into the Keras Graph Network.
      new_layers = base_layer_utils.create_keras_history(value)
      add_metric_layer = AddMetric(aggregation, name)
      add_metric_layer(value)
      new_layers.append(add_metric_layer)
      self._insert_layers(new_layers)

  @doc_controls.for_subclass_implementers
  def add_update(self, updates, inputs=None):
    """Add update op(s), potentially dependent on layer inputs.

    Weight updates (for instance, the updates of the moving mean and variance
    in a BatchNormalization layer) may be dependent on the inputs passed
    when calling a layer. Hence, when reusing the same layer on
    different inputs `a` and `b`, some entries in `layer.updates` may be
    dependent on `a` and some on `b`. This method automatically keeps track
    of dependencies.

    The `get_updates_for` method allows to retrieve the updates relevant to a
    specific set of inputs.

    This call is ignored when eager execution is enabled (in that case, variable
    updates are run on the fly and thus do not need to be tracked for later
    execution).

    Arguments:
      updates: Update op, or list/tuple of update ops, or zero-arg callable
        that returns an update op. A zero-arg callable should be passed in
        order to disable running the updates by setting `trainable=False`
        on this Layer, when executing in Eager mode.
      inputs: If anything other than None is passed, it signals the updates
        are conditional on some of the layer's inputs,
        and thus they should only be run where these inputs are available.
        This is the case for BatchNormalization updates, for instance.
        If None, the updates will be taken into account unconditionally,
        and you are responsible for making sure that any dependency they might
        have is available at runtime.
        A step counter might fall into this category.
    """
    updates = generic_utils.to_list(updates)

    # All updates can be run immediately in Eager or in a tf.function.
    if base_layer_utils.is_in_eager_or_tf_function():
      if not base_layer_utils.is_in_frozen_context():
        for update in updates:
          if callable(update):
            update()
      return

    def process_update(x):
      """Standardize update ops.

      Arguments:
        x: Tensor, op, or callable.

      Returns:
        An update op.
      """
      if callable(x):
        update = lambda: process_update(x())
        if not ops.executing_eagerly_outside_functions():
          # In V1 mode, call the callable right away and process. This is needed
          # for TPU strategy.
          return update()
      elif isinstance(x, ops.Operation):
        update = x
      elif hasattr(x, 'op'):
        update = x.op
      else:
        update = ops.convert_to_tensor(x)
      update._unconditional_update = (inputs is None)
      update._in_cross_replica_context = (
          ds_context.has_strategy() and ds_context.in_cross_replica_context())
      return update

    updates = [process_update(x) for x in updates]
    # Non-callable Updates are run automatically inside `call` in V2, so
    # they do not need to be tracked later.
    if (ops.executing_eagerly_outside_functions() and
        base_layer_utils.is_in_call_context()):
      updates = [u for u in updates if callable(u)]
    self._updates += updates

  def set_weights(self, weights):
    """Sets the weights of the layer, from Numpy arrays.

    Arguments:
        weights: a list of Numpy arrays. The number
            of arrays and their shape must match
            number of the dimensions of the weights
            of the layer (i.e. it should match the
            output of `get_weights`).

    Raises:
        ValueError: If the provided weights list does not match the
            layer's specifications.
    """
    params = self.weights
    if len(params) != len(weights):
      raise ValueError('You called `set_weights(weights)` on layer "' +
                       self.name + '" with a  weight list of length ' +
                       str(len(weights)) + ', but the layer was expecting ' +
                       str(len(params)) + ' weights. Provided weights: ' +
                       str(weights)[:50] + '...')
    if not params:
      return
    weight_value_tuples = []
    param_values = backend.batch_get_value(params)
    for pv, p, w in zip(param_values, params, weights):
      if pv.shape != w.shape:
        raise ValueError('Layer weight shape ' + str(pv.shape) +
                         ' not compatible with '
                         'provided weight shape ' + str(w.shape))
      weight_value_tuples.append((p, w))
    backend.batch_set_value(weight_value_tuples)

  def get_weights(self):
    """Returns the current weights of the layer.

    Returns:
        Weights values as a list of numpy arrays.
    """
    params = self.weights
    return backend.batch_get_value(params)

  def get_updates_for(self, inputs):
    """Retrieves updates relevant to a specific set of inputs.

    Arguments:
      inputs: Input tensor or list/tuple of input tensors.

    Returns:
      List of update ops of the layer that depend on `inputs`.
    """
    if inputs is None:
      # Requesting unconditional updates.
      return [u for u in self.updates if u._unconditional_update]

    # Requesting input-conditional updates.
    updates = [u for u in self.updates if not u._unconditional_update]
    inputs = nest.flatten(inputs)
    reachable = tf_utils.get_reachable_from_inputs(inputs, updates)
    return [u for u in updates if u in reachable]

  def get_losses_for(self, inputs):
    """Retrieves losses relevant to a specific set of inputs.

    Arguments:
      inputs: Input tensor or list/tuple of input tensors.

    Returns:
      List of loss tensors of the layer that depend on `inputs`.
    """
    if inputs is None:
      # Requesting unconditional losses.
      return [l for l in self.losses if l._unconditional_loss]

    # Requesting input-conditional losses.
    losses = [l for l in self.losses if not l._unconditional_loss]
    inputs = nest.flatten(inputs)
    reachable = tf_utils.get_reachable_from_inputs(inputs, losses)
    return [l for l in losses if l in reachable]

  def get_input_mask_at(self, node_index):
    """Retrieves the input mask tensor(s) of a layer at a given node.

    Arguments:
        node_index: Integer, index of the node
            from which to retrieve the attribute.
            E.g. `node_index=0` will correspond to the
            first time the layer was called.

    Returns:
        A mask tensor
        (or list of tensors if the layer has multiple inputs).
    """
    inputs = self.get_input_at(node_index)
    if isinstance(inputs, list):
      return [getattr(x, '_keras_mask', None) for x in inputs]
    else:
      return getattr(inputs, '_keras_mask', None)

  def get_output_mask_at(self, node_index):
    """Retrieves the output mask tensor(s) of a layer at a given node.

    Arguments:
        node_index: Integer, index of the node
            from which to retrieve the attribute.
            E.g. `node_index=0` will correspond to the
            first time the layer was called.

    Returns:
        A mask tensor
        (or list of tensors if the layer has multiple outputs).
    """
    output = self.get_output_at(node_index)
    if isinstance(output, list):
      return [getattr(x, '_keras_mask', None) for x in output]
    else:
      return getattr(output, '_keras_mask', None)

  @property
  def input_mask(self):
    """Retrieves the input mask tensor(s) of a layer.

    Only applicable if the layer has exactly one inbound node,
    i.e. if it is connected to one incoming layer.

    Returns:
        Input mask tensor (potentially None) or list of input
        mask tensors.

    Raises:
        AttributeError: if the layer is connected to
        more than one incoming layers.
    """
    inputs = self.input
    if isinstance(inputs, list):
      return [getattr(x, '_keras_mask', None) for x in inputs]
    else:
      return getattr(inputs, '_keras_mask', None)

  @property
  def output_mask(self):
    """Retrieves the output mask tensor(s) of a layer.

    Only applicable if the layer has exactly one inbound node,
    i.e. if it is connected to one incoming layer.

    Returns:
        Output mask tensor (potentially None) or list of output
        mask tensors.

    Raises:
        AttributeError: if the layer is connected to
        more than one incoming layers.
    """
    output = self.output
    if isinstance(output, list):
      return [getattr(x, '_keras_mask', None) for x in output]
    else:
      return getattr(output, '_keras_mask', None)

  def get_input_shape_at(self, node_index):
    """Retrieves the input shape(s) of a layer at a given node.

    Arguments:
        node_index: Integer, index of the node
            from which to retrieve the attribute.
            E.g. `node_index=0` will correspond to the
            first time the layer was called.

    Returns:
        A shape tuple
        (or list of shape tuples if the layer has multiple inputs).

    Raises:
      RuntimeError: If called in Eager mode.
    """
    return self._get_node_attribute_at_index(node_index, 'input_shapes',
                                             'input shape')

  def get_output_shape_at(self, node_index):
    """Retrieves the output shape(s) of a layer at a given node.

    Arguments:
        node_index: Integer, index of the node
            from which to retrieve the attribute.
            E.g. `node_index=0` will correspond to the
            first time the layer was called.

    Returns:
        A shape tuple
        (or list of shape tuples if the layer has multiple outputs).

    Raises:
      RuntimeError: If called in Eager mode.
    """
    return self._get_node_attribute_at_index(node_index, 'output_shapes',
                                             'output shape')

  def get_input_at(self, node_index):
    """Retrieves the input tensor(s) of a layer at a given node.

    Arguments:
        node_index: Integer, index of the node
            from which to retrieve the attribute.
            E.g. `node_index=0` will correspond to the
            first time the layer was called.

    Returns:
        A tensor (or list of tensors if the layer has multiple inputs).

    Raises:
      RuntimeError: If called in Eager mode.
    """
    return self._get_node_attribute_at_index(node_index, 'input_tensors',
                                             'input')

  def get_output_at(self, node_index):
    """Retrieves the output tensor(s) of a layer at a given node.

    Arguments:
        node_index: Integer, index of the node
            from which to retrieve the attribute.
            E.g. `node_index=0` will correspond to the
            first time the layer was called.

    Returns:
        A tensor (or list of tensors if the layer has multiple outputs).

    Raises:
      RuntimeError: If called in Eager mode.
    """
    return self._get_node_attribute_at_index(node_index, 'output_tensors',
                                             'output')

  @property
  def input(self):
    """Retrieves the input tensor(s) of a layer.

    Only applicable if the layer has exactly one input,
    i.e. if it is connected to one incoming layer.

    Returns:
        Input tensor or list of input tensors.

    Raises:
        AttributeError: if the layer is connected to
        more than one incoming layers.

    Raises:
      RuntimeError: If called in Eager mode.
      AttributeError: If no inbound nodes are found.
    """
    if not self._inbound_nodes:
      raise AttributeError('Layer ' + self.name +
                           ' is not connected, no input to return.')
    return self._get_node_attribute_at_index(0, 'input_tensors', 'input')

  @property
  def output(self):
    """Retrieves the output tensor(s) of a layer.

    Only applicable if the layer has exactly one output,
    i.e. if it is connected to one incoming layer.

    Returns:
      Output tensor or list of output tensors.

    Raises:
      AttributeError: if the layer is connected to more than one incoming
        layers.
      RuntimeError: if called in Eager mode.
    """
    if not self._inbound_nodes:
      raise AttributeError('Layer ' + self.name + ' has no inbound nodes.')
    return self._get_node_attribute_at_index(0, 'output_tensors', 'output')

  @property
  def input_shape(self):
    """Retrieves the input shape(s) of a layer.

    Only applicable if the layer has exactly one input,
    i.e. if it is connected to one incoming layer, or if all inputs
    have the same shape.

    Returns:
        Input shape, as an integer shape tuple
        (or list of shape tuples, one tuple per input tensor).

    Raises:
        AttributeError: if the layer has no defined input_shape.
        RuntimeError: if called in Eager mode.
    """
    if not self._inbound_nodes:
      raise AttributeError('The layer has never been called '
                           'and thus has no defined input shape.')
    all_input_shapes = set(
        [str(node.input_shapes) for node in self._inbound_nodes])
    if len(all_input_shapes) == 1:
      return self._inbound_nodes[0].input_shapes
    else:
      raise AttributeError('The layer "' + str(self.name) +
                           ' has multiple inbound nodes, '
                           'with different input shapes. Hence '
                           'the notion of "input shape" is '
                           'ill-defined for the layer. '
                           'Use `get_input_shape_at(node_index)` '
                           'instead.')

  def count_params(self):
    """Count the total number of scalars composing the weights.

    Returns:
        An integer count.

    Raises:
        ValueError: if the layer isn't yet built
          (in which case its weights aren't yet defined).
    """
    if not self.built:
      if self.__class__.__name__ == 'Sequential':
        with tf_utils.maybe_init_scope(self):
          self.build()  # pylint: disable=no-value-for-parameter
      else:
        raise ValueError('You tried to call `count_params` on ' + self.name +
                         ', but the layer isn\'t built. '
                         'You can build it manually via: `' + self.name +
                         '.build(batch_input_shape)`.')
    return int(sum(np.prod(w.shape.as_list()) for w in self.weights))

  @property
  def output_shape(self):
    """Retrieves the output shape(s) of a layer.

    Only applicable if the layer has one output,
    or if all outputs have the same shape.

    Returns:
        Output shape, as an integer shape tuple
        (or list of shape tuples, one tuple per output tensor).

    Raises:
        AttributeError: if the layer has no defined output shape.
        RuntimeError: if called in Eager mode.
    """
    if not self._inbound_nodes:
      raise AttributeError('The layer has never been called '
                           'and thus has no defined output shape.')
    all_output_shapes = set(
        [str(node.output_shapes) for node in self._inbound_nodes])
    if len(all_output_shapes) == 1:
      return self._inbound_nodes[0].output_shapes
    else:
      raise AttributeError('The layer "%s"'
                           ' has multiple inbound nodes, '
                           'with different output shapes. Hence '
                           'the notion of "output shape" is '
                           'ill-defined for the layer. '
                           'Use `get_output_shape_at(node_index)` '
                           'instead.' % self.name)

  @property
  @doc_controls.do_not_doc_inheritable
  def inbound_nodes(self):
    """Deprecated, do NOT use! Only for compatibility with external Keras."""
    return self._inbound_nodes

  @property
  @doc_controls.do_not_doc_inheritable
  def outbound_nodes(self):
    """Deprecated, do NOT use! Only for compatibility with external Keras."""
    return self._outbound_nodes

  ##############################################################################
  # Methods & attributes below are public aliases of other methods.            #
  ##############################################################################

  def apply(self, inputs, *args, **kwargs):
    """Apply the layer on a input.

    This is an alias of `self.__call__`.

    Arguments:
      inputs: Input tensor(s).
      *args: additional positional arguments to be passed to `self.call`.
      **kwargs: additional keyword arguments to be passed to `self.call`.

    Returns:
      Output tensor(s).
    """
    return self.__call__(inputs, *args, **kwargs)

  @doc_controls.for_subclass_implementers
  def add_variable(self, *args, **kwargs):
    """Alias for `add_weight`."""
    return self.add_weight(*args, **kwargs)

  @property
  def variables(self):
    """Returns the list of all layer variables/weights.

    Alias of `self.weights`.

    Returns:
      A list of variables.
    """
    return self.weights

  @property
  def trainable_variables(self):
    return self.trainable_weights

  @property
  def non_trainable_variables(self):
    return self.non_trainable_weights

  ##############################################################################
  # Methods & attributes below are all private and only used by the framework. #
  ##############################################################################

  def _set_dtype_and_policy(self, dtype):
    """Sets self._dtype and self._mixed_precision_policy."""
    if dtype:
      if isinstance(dtype, policy.Policy):
        self._mixed_precision_policy = dtype
        self._dtype = self._mixed_precision_policy.default_variable_dtype
      else:
        # If a non-policy dtype is passed, no casting should be done. So we use
        # the "infer" policy, which does no casting.
        self._mixed_precision_policy = policy.Policy('infer')
        self._dtype = dtypes.as_dtype(dtype).name
    else:
      self._mixed_precision_policy = policy.global_policy()
      # If the global policy has not been set, it will be an "infer" policy
      # without a default variable dtype, and so self._dtype will be None. In
      # that case, self._dtype will be set when the layer is built or called.
      self._dtype = self._mixed_precision_policy.default_variable_dtype

  def _name_scope(self):
    return self.name

  def _init_set_name(self, name, zero_based=True):
    if not name:
      self._name = backend.unique_object_name(
          generic_utils.to_snake_case(self.__class__.__name__),
          zero_based=zero_based)
    else:
      self._name = name

  def _get_existing_metric(self, name=None):
    match = [m for m in self._metrics if m.name == name]
    if not match:
      return
    if len(match) > 1:
      raise ValueError(
          'Please provide different names for the metrics you have added. '
          'We found {} metrics with the name: "{}"'.format(len(match), name))
    return match[0]

  def _eager_add_metric(self, value, aggregation=None, name=None):
    # If the given metric is available in `metrics` list we just update state
    # on it, otherwise we create a new metric instance and
    # add it to the `metrics` list.
    metric_obj = getattr(value, '_metric_obj', None)
    if metric_obj:
      name = metric_obj.name

    match = self._get_existing_metric(name)
    if match:
      # Tensors that come from a Metric object already updated the Metric state.
      if not metric_obj:
        match(value)
      return

    if not metric_obj:
      assert aggregation is not None
      metric_obj, _ = base_layer_utils.create_mean_metric(value, name)
    self._metrics.append(metric_obj)

  def _symbolic_add_metric(self, value, aggregation=None, name=None):
    base_layer_utils.check_graph_consistency(value, method='add_metric')
    match = self._get_existing_metric(name)
    if aggregation is None:
      # Iterate over the metrics and check if the given metric exists already.
      # This can happen when a metric instance is created in subclassed model
      # layer `__init__` and we have tracked that instance already in
      # model.__setattr__.
      if match:
        result_tensor = value
        metric_obj = match
      elif hasattr(value, '_metric_obj'):
        # We track the instance using the metadata on the result tensor.
        result_tensor = value
        metric_obj = result_tensor._metric_obj
        self._metrics.append(metric_obj)
      else:
        raise ValueError(
            'We do not support adding an aggregated metric result tensor that '
            'is not the output of a `tf.keras.metrics.Metric` metric instance. '
            'Without having access to the metric instance we cannot reset the '
            'state of a metric after every epoch during training. You can '
            'create a `tf.keras.metrics.Metric` instance and pass the result '
            'here or pass an un-aggregated result with `aggregation` parameter '
            'set as `mean`. For example: `self.add_metric(tf.reduce_sum(inputs)'
            ', name=\'mean_activation\', aggregation=\'mean\')`')
    else:
      # If a non-aggregated tensor is given as input (ie. `aggregation` is
      # explicitly set to `mean`), we wrap the tensor in `Mean` metric.
      if match:
        result_tensor = match(value)
        metric_obj = match
      else:
        metric_obj, result_tensor = base_layer_utils.create_mean_metric(
            value, name)
        self._metrics.append(metric_obj)
    self._metrics_tensors[metric_obj.name] = result_tensor

  def _handle_weight_regularization(self, name, variable, regularizer):
    """Create lambdas which compute regularization losses."""

    def _loss_for_variable(v):
      """Creates a regularization loss `Tensor` for variable `v`."""
      with backend.name_scope(name + '/Regularizer'):
        regularization = regularizer(v)
      return regularization

    if isinstance(variable, tf_variables.PartitionedVariable):
      for v in variable:
        self.add_loss(functools.partial(_loss_for_variable, v))
    else:
      self.add_loss(functools.partial(_loss_for_variable, variable))

  def _handle_activity_regularization(self, inputs, outputs):
    # Apply activity regularization.
    # Note that it should be applied every time the layer creates a new
    # output, since it is output-specific.
    if self._activity_regularizer:
      output_list = nest.flatten(outputs)
      with backend.name_scope('ActivityRegularizer'):
        for output in output_list:
          activity_loss = self._activity_regularizer(output)
          batch_size = math_ops.cast(
              array_ops.shape(output)[0], activity_loss.dtype)
          # Make activity regularization strength batch-agnostic.
          mean_activity_loss = activity_loss / batch_size
          self.add_loss(mean_activity_loss, inputs=inputs)

  def _set_mask_metadata(self, inputs, outputs, previous_mask):
    flat_outputs = nest.flatten(outputs)
    mask_already_computed = (
        getattr(self, '_compute_output_and_mask_jointly', False) or
        all(getattr(x, '_keras_mask', None) is not None for x in flat_outputs))

    if not mask_already_computed:
      if hasattr(self, 'compute_mask'):
        output_masks = self.compute_mask(inputs, previous_mask)
        # `compute_mask` can return a single `None` even when a Layer
        # has multiple outputs.
        if output_masks is None:
          flat_masks = [None for _ in flat_outputs]
        else:
          flat_masks = nest.flatten(output_masks)
      else:
        flat_masks = [None for _ in flat_outputs]

      for output, mask in zip(flat_outputs, flat_masks):
        try:
          output._keras_mask = mask
        except AttributeError:
          # C Type such as np.ndarray.
          pass

    if tf_utils.are_all_symbolic_tensors(flat_outputs):
      for output in flat_outputs:
        if getattr(output, '_keras_mask', None) is not None:
          # Do not track masks for `TensorFlowOpLayer` construction.
          output._keras_mask._keras_history_checked = True

  def _set_connectivity_metadata_(self, inputs, outputs, args, kwargs):
    call_convention = getattr(
        self, '_call_convention',
        base_layer_utils.CallConvention.EXPLICIT_INPUTS_ARGUMENT)
    if args:
      if call_convention == (base_layer_utils
                             .CallConvention.EXPLICIT_INPUTS_ARGUMENT):
        raise TypeError(
            'This layer ("{}") takes an `inputs` argument in `call()`, '
            'and only the `inputs` argument may be specified as a positional '
            'argument. Pass everything else as a keyword argument '
            '(those arguments will not be tracked '
            'as inputs to the layer).'.format(self.name))
      elif call_convention == (base_layer_utils
                               .CallConvention.SINGLE_POSITIONAL_ARGUMENT):
        raise TypeError(
            'This layer ("{}") takes a single positional argument in `call()`,'
            ' which is by convention the `inputs` argument, '
            'and only this argument may be specified as a positional argument. '
            'Pass everything else as a keyword argument '
            '(those arguments will not be tracked '
            'as inputs to the layer).'.format(self.name))

    # If the layer returns tensors from its inputs, unmodified,
    # we copy them to avoid loss of tensor metadata.
    output_ls = nest.flatten(outputs)
    inputs_ls = nest.flatten(inputs)
    output_ls_copy = []
    for x in output_ls:
      if x in inputs_ls:
        with backend.name_scope(self.name):
          x = array_ops.identity(x)
      output_ls_copy.append(x)
    outputs = nest.pack_sequence_as(outputs, output_ls_copy)

    inputs, kwargs = self._inputs_from_call_args(
        call_args=(inputs,) + args, call_kwargs=kwargs)
    # Add an inbound node to the layer, so it can keep track of this call.
    # This updates the layer history of the output tensor(s).
    kwargs.pop('mask', None)  # `mask` should not be serialized.
    self._add_inbound_node(
        input_tensors=inputs, output_tensors=outputs, arguments=kwargs)
    return inputs, outputs

  def _inputs_from_call_args(self, call_args, call_kwargs):
    """Get Layer inputs from __call__ *args and **kwargs.

    Args:
      call_args: The positional arguments passed to __call__.
      call_kwargs: The keyword argument dict passed to __call__.

    Returns:
      A tuple of (inputs, non_input_kwargs). These may be the same objects as
      were passed in (call_args and call_kwargs).
    """
    call_convention = getattr(
        self, '_call_convention',
        base_layer_utils.CallConvention.EXPLICIT_INPUTS_ARGUMENT)
    if (call_convention in (
        base_layer_utils.CallConvention.EXPLICIT_INPUTS_ARGUMENT,
        base_layer_utils.CallConvention.SINGLE_POSITIONAL_ARGUMENT)):
      assert len(call_args) == 1  # TypeError raised earlier in __call__.
      return call_args[0], call_kwargs
    else:
      call_arg_spec = tf_inspect.getfullargspec(self.call)
      # There is no explicit "inputs" argument expected or provided to
      # call(). Arguments which have default values are considered non-inputs,
      # and arguments without are considered inputs.
      if call_arg_spec.defaults:
        if call_arg_spec.varargs is not None:
          raise TypeError(
              'Layers may not accept both positional arguments and '
              'arguments with default values (unable to determine which '
              'are inputs to the layer). '
              'Issue occurred with layer "%s"' % (self.name))
        keyword_arg_names = set(
            call_arg_spec.args[-len(call_arg_spec.defaults):])
      else:
        keyword_arg_names = set()
        # Training is never an input argument name, to allow signatures like
        # call(x, training).
      keyword_arg_names.add('training')
      _, unwrapped_call = tf_decorator.unwrap(self.call)
      bound_args = inspect.getcallargs(
          unwrapped_call, *call_args, **call_kwargs)
      if call_arg_spec.varkw is not None:
        var_kwargs = bound_args.pop(call_arg_spec.varkw)
        bound_args.update(var_kwargs)
        keyword_arg_names = keyword_arg_names.union(var_kwargs.keys())
      all_args = call_arg_spec.args
      if all_args and bound_args[all_args[0]] is self:
        # Ignore the 'self' argument of methods
        bound_args.pop(call_arg_spec.args[0])
        all_args = all_args[1:]
      non_input_arg_values = {}
      input_arg_values = []
      remaining_args_are_keyword = False
      for argument_name in all_args:
        if argument_name in keyword_arg_names:
          remaining_args_are_keyword = True
        else:
          if remaining_args_are_keyword:
            raise TypeError(
                'Found a positional argument in a layer call after a non-input '
                'argument. All arguments after "training" must be keyword '
                'arguments, and are not tracked as inputs to the layer. '
                'Issue occurred with layer "%s"' % (self.name))
        if remaining_args_are_keyword:
          non_input_arg_values[argument_name] = bound_args[argument_name]
        else:
          input_arg_values.append(bound_args[argument_name])
      if call_arg_spec.varargs is not None:
        input_arg_values.extend(bound_args[call_arg_spec.varargs])
      return input_arg_values, non_input_arg_values

  def _add_inbound_node(self,
                        input_tensors,
                        output_tensors,
                        arguments=None):
    """Internal method to create an inbound node for the layer.

    Arguments:
        input_tensors: list of input tensors.
        output_tensors: list of output tensors.
        arguments: dictionary of keyword arguments that were passed to the
            `call` method of the layer at the call that created the node.
    """
    inbound_layers = nest.map_structure(lambda t: t._keras_history.layer,
                                        input_tensors)
    node_indices = nest.map_structure(lambda t: t._keras_history.node_index,
                                      input_tensors)
    tensor_indices = nest.map_structure(lambda t: t._keras_history.tensor_index,
                                        input_tensors)

    # Create node, add it to inbound nodes.
    Node(
        self,
        inbound_layers=inbound_layers,
        node_indices=node_indices,
        tensor_indices=tensor_indices,
        input_tensors=input_tensors,
        output_tensors=output_tensors,
        arguments=arguments)

    # Update tensor history metadata.
    # The metadata attribute consists of
    # 1) a layer instance
    # 2) a node index for the layer
    # 3) a tensor index for the node.
    # The allows layer reuse (multiple nodes per layer) and multi-output
    # or multi-input layers (e.g. a layer can return multiple tensors,
    # and each can be sent to a different layer).
    for i, tensor in enumerate(nest.flatten(output_tensors)):
      tensor._keras_history = KerasHistory(self,
                                           len(self._inbound_nodes) - 1, i)  # pylint: disable=protected-access

  def _get_node_attribute_at_index(self, node_index, attr, attr_name):
    """Private utility to retrieves an attribute (e.g. inputs) from a node.

    This is used to implement the methods:
        - get_input_shape_at
        - get_output_shape_at
        - get_input_at
        etc...

    Arguments:
        node_index: Integer index of the node from which
            to retrieve the attribute.
        attr: Exact node attribute name.
        attr_name: Human-readable attribute name, for error messages.

    Returns:
        The layer's attribute `attr` at the node of index `node_index`.

    Raises:
        RuntimeError: If the layer has no inbound nodes, or if called in Eager
        mode.
        ValueError: If the index provided does not match any node.
    """
    if not self._inbound_nodes:
      raise RuntimeError('The layer has never been called '
                         'and thus has no defined ' + attr_name + '.')
    if not len(self._inbound_nodes) > node_index:
      raise ValueError('Asked to get ' + attr_name + ' at node ' +
                       str(node_index) + ', but the layer has only ' +
                       str(len(self._inbound_nodes)) + ' inbound nodes.')
    values = getattr(self._inbound_nodes[node_index], attr)
    if isinstance(values, list) and len(values) == 1:
      return values[0]
    else:
      return values

  def _maybe_build(self, inputs):
    # Check input assumptions set before layer building, e.g. input rank.
    if self.built:
      return

    input_spec.assert_input_compatibility(
        self.input_spec, inputs, self.name)
    input_list = nest.flatten(inputs)
    if input_list and self._dtype is None:
      try:
        self._dtype = input_list[0].dtype.base_dtype.name
      except AttributeError:
        pass
    input_shapes = None
    if all(hasattr(x, 'shape') for x in input_list):
      input_shapes = nest.map_structure(lambda x: x.shape, inputs)
    # Only call `build` if the user has manually overridden the build method.
    if not hasattr(self.build, '_is_default'):
      # Any setup work performed only once should happen in an `init_scope`
      # to avoid creating symbolic Tensors that will later pollute any eager
      # operations.
      with tf_utils.maybe_init_scope(self):
        self.build(input_shapes)
    # We must set self.built since user defined build functions are not
    # constrained to set self.built.
    self.built = True

  def _symbolic_call(self, inputs):
    input_shapes = nest.map_structure(lambda x: x.shape, inputs)
    output_shapes = self.compute_output_shape(input_shapes)

    def _make_placeholder_like(shape):
      ph = backend.placeholder(shape=shape, dtype=self.dtype)
      ph._keras_mask = None
      return ph

    return nest.map_structure(_make_placeholder_like, output_shapes)

  @property
  def _obj_reference_counts(self):
    """A dictionary counting the number of attributes referencing an object."""
    self._maybe_create_attribute('_obj_reference_counts_dict',
                                 object_identity.ObjectIdentityDictionary())
    return self._obj_reference_counts_dict

  def _maybe_create_attribute(self, name, default_value):
    """Create the attribute with the default value if it hasn't been created.

    This is useful for fields that is used for tracking purpose,
    _trainable_weights, or _layers. Note that user could create a layer subclass
    and assign an internal field before invoking the Layer.__init__(), the
    __setattr__() need to create the tracking fields and __init__() need to not
    override them.

    Args:
      name: String, the name of the attribute.
      default_value: Object, the default value of the attribute.
    """
    if not hasattr(self, name):
      super(Layer, self).__setattr__(name, default_value)

  def __delattr__(self, name):
    # For any super.__delattr__() call, we will directly use the implementation
    # in Trackable and skip the behavior in AutoTrackable. The Layer was
    # originally use Trackable as base class, the change of using Module as base
    # class forced us to have AutoTrackable in the class hierarchy. Skipping
    # the __delattr__ and __setattr__ in AutoTrackable will keep the status quo.
    existing_value = getattr(self, name, None)

    # If this value is replacing an existing object assigned to an attribute, we
    # should clean it out to avoid leaking memory. First we check if there are
    # other attributes referencing it.
    reference_counts = self._obj_reference_counts
    if existing_value not in reference_counts:
      super(tracking.AutoTrackable, self).__delattr__(name)
      return

    reference_count = reference_counts[existing_value]
    if reference_count > 1:
      # There are other remaining references. We can't remove this object from
      # _layers etc.
      reference_counts[existing_value] = reference_count - 1
      super(tracking.AutoTrackable, self).__delattr__(name)
      return
    else:
      # This is the last remaining reference.
      del reference_counts[existing_value]

    super(tracking.AutoTrackable, self).__delattr__(name)

    if (isinstance(existing_value, Layer)
        or trackable_layer_utils.has_weights(existing_value)):
      super(tracking.AutoTrackable, self).__setattr__(
          '_layers',
          [l for l in self._layers if l is not existing_value])
    if isinstance(existing_value, tf_variables.Variable):
      super(tracking.AutoTrackable, self).__setattr__(
          '_trainable_weights',
          [w for w in self._trainable_weights if w is not existing_value])
      super(tracking.AutoTrackable, self).__setattr__(
          '_non_trainable_weights',
          [w for w in self._non_trainable_weights if w is not existing_value])

  def __setattr__(self, name, value):
    if (name == '_self_setattr_tracking' or
        not getattr(self, '_self_setattr_tracking', True) or
        getattr(self, '_is_graph_network', False) or
        # Exclude @property.setters from tracking
        hasattr(self.__class__, name)):
      try:
        super(tracking.AutoTrackable, self).__setattr__(name, value)
      except AttributeError:
        raise AttributeError(
            ('Can\'t set the attribute "{}", likely because it conflicts with '
             'an existing read-only @property of the object. Please choose a '
             'different name.').format(name))
      return

    # Keep track of trackable objects, for the needs of `Network.save_weights`.
    value = data_structures.sticky_attribute_assignment(
        trackable=self, value=value, name=name)

    reference_counts = self._obj_reference_counts
    reference_counts[value] = reference_counts.get(value, 0) + 1

    # Clean out the old attribute, which clears _layers and _trainable_weights
    # if necessary.
    try:
      self.__delattr__(name)
    except AttributeError:
      pass

    # TODO(scottzhu): Need to track Module object as well for weight tracking.
    # Be careful about metric if it becomes a Module in future.
    # Append value to self._layers if relevant
    if (isinstance(value, Layer) or
        trackable_layer_utils.has_weights(value)):
      self._maybe_create_attribute('_layers', [])
      # We need to check object identity to avoid de-duplicating empty
      # container types which compare equal.
      if not any((layer is value for layer in self._layers)):
        self._layers.append(value)
        if hasattr(value, '_use_resource_variables'):
          # Legacy layers (V1 tf.layers) must always use
          # resource variables.
          value._use_resource_variables = True

    # Append value to list of trainable / non-trainable weights if relevant
    # TODO(b/125122625): This won't pick up on any variables added to a
    # list/dict after creation.
    for val in nest.flatten(value):
      # TODO(b/126450014): Remove `_UnreadVariable` check here when assign ops
      # no longer return True for isinstance Variable checks.
      if (isinstance(val, tf_variables.Variable) and
          not isinstance(val, resource_variable_ops._UnreadVariable)):  # pylint: disable=protected-access
        # Users may add extra weights/variables
        # simply by assigning them to attributes (invalid for graph networks)
        self._maybe_create_attribute('_trainable_weights', [])
        self._maybe_create_attribute('_non_trainable_weights', [])
        if val not in self._trainable_weights + self._non_trainable_weights:
          if val.trainable:
            self._trainable_weights.append(val)
          else:
            self._non_trainable_weights.append(val)
          backend.track_variable(val)

    # Skip the auto trackable from tf.Module to keep status quo. See the comment
    # at __delattr__.
    super(tracking.AutoTrackable, self).__setattr__(name, value)

  def _gather_children_attribute(self, attribute):
    assert attribute in {
        'weights', 'trainable_weights', 'non_trainable_weights', 'updates',
        'losses', 'metrics'
    }
    if hasattr(self, '_layers'):
      nested_layers = trackable_layer_utils.filter_empty_layer_containers(
          self._layers)
      return list(
          itertools.chain.from_iterable(
              getattr(layer, attribute) for layer in nested_layers))
    return []

  # This is a hack so that the is_layer (within
  # training/trackable/layer_utils.py) check doesn't get the weights attr.
  # TODO(b/110718070): Remove when fixed.
  def _is_layer(self):
    return True

  @property
  @tracking.cached_per_instance
  def _call_fn_args(self):
    return function_utils.fn_args(self.call)

  @property
  @tracking.cached_per_instance
  def _should_compute_mask(self):
    return ('mask' in self._call_fn_args or
            getattr(self, 'compute_mask', None) is not None)


class Node(object):
  """A `Node` describes the connectivity between two layers.

  Each time a layer is connected to some new input,
  a node is added to `layer._inbound_nodes`.
  Each time the output of a layer is used by another layer,
  a node is added to `layer._outbound_nodes`.

  Arguments:
      outbound_layer: the layer that takes
          `input_tensors` and turns them into `output_tensors`
          (the node gets created when the `call`
          method of the layer was called).
      inbound_layers: a list of layers, the same length as `input_tensors`,
          the layers from where `input_tensors` originate.
      node_indices: a list of integers, the same length as `inbound_layers`.
          `node_indices[i]` is the origin node of `input_tensors[i]`
          (necessary since each inbound layer might have several nodes,
          e.g. if the layer is being shared with a different data stream).
      tensor_indices: a list of integers,
          the same length as `inbound_layers`.
          `tensor_indices[i]` is the index of `input_tensors[i]` within the
          output of the inbound layer
          (necessary since each inbound layer might
          have multiple tensor outputs, with each one being
          independently manipulable).
      input_tensors: list of input tensors.
      output_tensors: list of output tensors.
      arguments: dictionary of keyword arguments that were passed to the
          `call` method of the layer at the call that created the node.

  `node_indices` and `tensor_indices` are basically fine-grained coordinates
  describing the origin of the `input_tensors`.

  A node from layer A to layer B is added to:
    - A._outbound_nodes
    - B._inbound_nodes
  """

  def __init__(self,
               outbound_layer,
               inbound_layers,
               node_indices,
               tensor_indices,
               input_tensors,
               output_tensors,
               arguments=None):
    # Layer instance (NOT a sequence)
    if isinstance(outbound_layer, (list, tuple, dict)):
      raise ValueError('`outbound_layer` should be a layer instance, '
                       'not a list, tuple, or, dict.')

    # this is the layer that takes a nested structure of input tensors
    # and turns them into a nested structure of output tensors.
    # the current node will be added to
    # the inbound_nodes of outbound_layer.
    self.outbound_layer = outbound_layer

    # The following 3 properties describe where
    # the input tensors come from: which layers,
    # and for each layer, which node and which
    # tensor output of each node.

    # Nested structure of layer instances.
    self.inbound_layers = inbound_layers
    # Nested structure of integers, 1:1 mapping with inbound_layers.
    self.node_indices = node_indices
    # Nested of integers, 1:1 mapping with inbound_layers.
    self.tensor_indices = tensor_indices

    # Following 2 properties:
    # tensor inputs and outputs of outbound_layer.

    # Nested structure of tensors. 1:1 mapping with inbound_layers.
    self.input_tensors = input_tensors
    # Nested structure of tensors, created by outbound_layer.call().
    self.output_tensors = output_tensors

    # Following 2 properties: input and output shapes.

    # Nested structure of shape tuples, shapes of input_tensors.
    self.input_shapes = nest.map_structure(backend.int_shape, input_tensors)
    # Nested structure of shape tuples, shapes of output_tensors.
    self.output_shapes = nest.map_structure(backend.int_shape, output_tensors)

    # Optional keyword arguments to layer's `call`.
    self.arguments = arguments

    # Add nodes to all layers involved.
    for layer in nest.flatten(inbound_layers):
      if layer is not None:
        # For compatibility with external Keras, we use the deprecated
        # accessor here.
        layer.outbound_nodes.append(self)
    # For compatibility with external Keras, we use the deprecated
    # accessor here.
    outbound_layer.inbound_nodes.append(self)

  def iterate_inbound(self):
    """Returns a list of tuples representing the inbound data.

    Returns:
      List of tuples like: (inbound_layer, node_index, tensor_index, tensor).
    """
    return zip(
        nest.flatten(self.inbound_layers), nest.flatten(self.node_indices),
        nest.flatten(self.tensor_indices), nest.flatten(self.input_tensors))

  def get_config(self):
    inbound_names = nest.map_structure(
        lambda layer: layer.name if layer else None, self.inbound_layers)
    return {
        'outbound_layer': self.outbound_layer.name,
        'inbound_layers': inbound_names,
        'node_indices': self.node_indices,
        'tensor_indices': self.tensor_indices
    }


class TensorFlowOpLayer(Layer):
  """Wraps a TensorFlow Operation in a Layer.

  This class is used internally by the Functional API. When a user
  uses a raw TensorFlow Operation on symbolic tensors originating
  from an `Input` Layer, the resultant operation will be wrapped
  with this Layer object in order to make the operation compatible
  with the Keras API.

  This Layer will create a new, identical operation (except for inputs
  and outputs) every time it is called. If `run_eagerly` is `True`,
  the op creation and calculation will happen inside an Eager function.

  Instances of this Layer are created when `autolambda` is called, which
  is whenever a Layer's `__call__` encounters symbolic inputs that do
  not have Keras metadata, or when a Network's `__init__` encounters
  outputs that do not have Keras metadata.

  Attributes:
    node_def: String, the serialized NodeDef of the Op this layer will wrap.
    constants: Dict of NumPy arrays, the values of any Tensors needed for this
      Operation that do not originate from a Keras `Input` Layer. Since all
      placeholders must come from Keras `Input` Layers, these Tensors must be
      treated as constant in the Functional API.
    name: String, the name of the Layer.
    trainable: Bool, whether this Layer is trainable. Currently Variables are
      not supported, and so this parameter has no effect.
    dtype: The default dtype of this Layer. Inherited from `Layer` and has no
      effect on this class, however is used in `get_config`.
  """

  def __init__(self,
               node_def,
               constants=None,
               name=None,
               trainable=True,
               dtype=None):
    super(TensorFlowOpLayer, self).__init__(
        name=_TF_OP_LAYER_NAME_PREFIX + name, trainable=trainable, dtype=dtype)
    self.node_def = node_def_pb2.NodeDef.FromString(node_def)
    self.constants = constants or {}
    # Layer uses original op unless it is called on new inputs.
    # This means `built` is not set in `__call__`.
    self.built = True

  def call(self, inputs):
    if context.executing_eagerly():
      return self._defun_call(inputs)
    return self._make_op(inputs)

  def _make_op(self, inputs):
    inputs = nest.flatten(inputs)
    graph = inputs[0].graph
    with graph.as_default():
      for index, constant in self.constants.items():
        constant = ops.convert_to_tensor(constant)
        inputs.insert(index, constant)

      self.node_def.name = graph.unique_name(self.node_def.name)
      # Check for case where first input should be a list of Tensors.
      if 'N' in self.node_def.attr:
        num_tensors = self.node_def.attr['N'].i
        inputs = [inputs[:num_tensors]] + inputs[num_tensors:]
      c_op = ops._create_c_op(graph, self.node_def, inputs, control_inputs=[])
      op = graph._create_op_from_tf_operation(c_op)

      # Record the gradient because custom-made ops don't go through the
      # code-gen'd eager call path
      op_type = compat.as_str(op.op_def.name)
      attr_names = [compat.as_str(attr.name) for attr in op.op_def.attr]
      attrs = []
      for attr_name in attr_names:
        attrs.append(attr_name)
        attrs.append(op.get_attr(attr_name))
      attrs = tuple(attrs)
      execute.record_gradient(op_type, op.inputs, attrs, op.outputs,
                              op.name)

      if len(op.outputs) == 1:
        return op.outputs[0]
      return op.outputs

  @function.defun
  def _defun_call(self, inputs):
    """Wraps the op creation method in an Eager function for `run_eagerly`."""
    return self._make_op(inputs)

  def get_config(self):
    config = super(TensorFlowOpLayer, self).get_config()
    config.update({
        'node_def': self.node_def.SerializeToString(),
        'constants': {
            i: backend.get_value(c) for i, c in self.constants.items()
        }
    })
    return config


class AddLoss(Layer):
  """Adds its inputs as a loss.

  Attributes:
    unconditional: Whether or not the loss should be conditioned on the inputs.
  """

  def __init__(self, unconditional, **kwargs):
    super(AddLoss, self).__init__(**kwargs)
    self.unconditional = unconditional

  def call(self, inputs):
    self.add_loss(inputs, inputs=(not self.unconditional))
    return inputs

  def get_config(self):
    config = super(AddLoss, self).get_config()
    config.update({'unconditional': self.unconditional})
    return config


class AddMetric(Layer):
  """Adds its inputs as a metric.

  Attributes:
    aggregation: 'mean' or None. How the inputs should be aggregated.
    metric_name: The name to use for this metric.
  """

  def __init__(self, aggregation=None, metric_name=None, **kwargs):
    super(AddMetric, self).__init__(**kwargs)
    self.aggregation = aggregation
    self.metric_name = metric_name

  def call(self, inputs):
    self.add_metric(inputs, self.aggregation, self.metric_name)
    return inputs

  def get_config(self):
    config = super(AddMetric, self).get_config()
    config.update({
        'aggregation': self.aggregation,
        'metric_name': self.metric_name
    })
    return config


class KerasHistory(
    collections.namedtuple('KerasHistory',
                           ['layer', 'node_index', 'tensor_index'])):
  """Tracks the Layer call that created a Tensor, for Keras Graph Networks.

  During construction of Keras Graph Networks, this metadata is added to
  each Tensor produced as the output of a Layer, starting with an
  `InputLayer`. This allows Keras to track how each Tensor was produced, and
  this information is later retraced by the `keras.engine.Network` class to
  reconstruct the Keras Graph Network.

  Attributes:
    layer: The Layer that produced the Tensor.
    node_index: The specific call to the Layer that produced this Tensor. Layers
      can be called multiple times in order to share weights. A new node is
      created every time a Tensor is called.
    tensor_index: The output index for this Tensor. Always zero if the Layer
      that produced this Tensor only has one output. Nested structures of
      Tensors are deterministically assigned an index via `nest.flatten`.
  """
  # Added to maintain memory and performance characteristics of `namedtuple`
  # while subclassing.
  __slots__ = ()


def default(method):
  """Decorates a method to detect overrides in subclasses."""
  method._is_default = True
  return method


# Avoid breaking users who directly import this symbol from this file.
# TODO(fchollet): remove this.
InputSpec = input_spec.InputSpec  # pylint:disable=invalid-name
