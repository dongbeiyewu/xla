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
"""Library for running a computation across multiple devices."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import copy
import enum  # pylint: disable=g-bad-import-order
import threading
import weakref
import six

from tensorflow.python.data.ops import dataset_ops
from tensorflow.python.distribute import device_util
from tensorflow.python.distribute import distribution_strategy_context
from tensorflow.python.distribute import numpy_dataset
from tensorflow.python.distribute import reduce_util
from tensorflow.python.eager import context as eager_context
from tensorflow.python.framework import constant_op
from tensorflow.python.framework import dtypes
from tensorflow.python.framework import ops
from tensorflow.python.framework import tensor_shape
from tensorflow.python.ops import array_ops
from tensorflow.python.ops import control_flow_ops
from tensorflow.python.ops import custom_gradient
from tensorflow.python.ops import math_ops
from tensorflow.python.ops import resource_variable_ops
from tensorflow.python.ops import variable_scope
from tensorflow.python.ops.losses import loss_reduction
from tensorflow.python.ops.losses import losses_impl
from tensorflow.python.platform import tf_logging
from tensorflow.python.util import nest
from tensorflow.python.util import tf_contextlib
from tensorflow.python.util.tf_export import tf_export
from tensorflow.tools.docs import doc_controls


# ------------------------------------------------------------------------------
# Context tracking whether in a strategy.update() or .update_non_slot() call.


_update_device = threading.local()


def get_update_device():
  """Get the current device if in a `tf.distribute.Strategy.update()` call."""
  try:
    return _update_device.current
  except AttributeError:
    return None


class UpdateContext(object):
  """Context manager when you are in `update()` or `update_non_slot()`."""

  def __init__(self, device):
    self._device = device
    self._old_device = None

  def __enter__(self):
    self._old_device = get_update_device()
    _update_device.current = self._device

  def __exit__(self, exception_type, exception_value, traceback):
    del exception_type, exception_value, traceback
    _update_device.current = self._old_device


# ------------------------------------------------------------------------------
# Public utility functions.


@tf_export(v1=["distribute.get_loss_reduction"])
def get_loss_reduction():
  """`tf.distribute.ReduceOp` corresponding to the last loss reduction.

  This is used to decide whether loss should be scaled in optimizer (used only
  for estimator + v1 optimizer use case).

  Returns:
    `tf.distribute.ReduceOp` corresponding to the last loss reduction for
    estimator and v1 optimizer use case. `tf.distribute.ReduceOp.SUM` otherwise.
  """
  if not distribution_strategy_context.get_strategy()._scale_loss_for_estimator:  # pylint: disable=protected-access
    # If we are not in Estimator context then return 'SUM'. We do not need to
    # scale loss in the optimizer.
    return reduce_util.ReduceOp.SUM
  last_reduction = ops.get_default_graph()._last_loss_reduction  # pylint: disable=protected-access
  if (last_reduction == losses_impl.Reduction.SUM or
      last_reduction == loss_reduction.ReductionV2.SUM):
    return reduce_util.ReduceOp.SUM
  return reduce_util.ReduceOp.MEAN


# ------------------------------------------------------------------------------
# Internal API for validating the current thread mode


def _require_cross_replica_or_default_context_extended(extended):
  """Verify in cross-replica context."""
  context = _get_per_thread_mode()
  cross_replica = context.cross_replica_context
  if cross_replica is not None and cross_replica.extended is extended:
    return
  if context is _get_default_replica_mode():
    return
  strategy = extended._container_strategy()  # pylint: disable=protected-access
  # We have an error to report, figure out the right message.
  if context.strategy is not strategy:
    _wrong_strategy_scope(strategy, context)
  assert cross_replica is None
  raise RuntimeError("Method requires being in cross-replica context, use "
                     "get_replica_context().merge_call()")


def _wrong_strategy_scope(strategy, context):
  # Figure out the right error message.
  if not distribution_strategy_context.has_strategy():
    raise RuntimeError(
        'Need to be inside "with strategy.scope()" for %s' %
        (strategy,))
  else:
    raise RuntimeError(
        "Mixing different tf.distribute.Strategy objects: %s is not %s" %
        (context.strategy, strategy))


def require_replica_context(replica_ctx):
  """Verify in `replica_ctx` replica context."""
  context = _get_per_thread_mode()
  if context.replica_context is replica_ctx: return
  # We have an error to report, figure out the right message.
  if context.replica_context is None:
    raise RuntimeError("Need to be inside `call_for_each_replica()`")
  if context.strategy is replica_ctx.strategy:
    # Two different ReplicaContexts with the same tf.distribute.Strategy.
    raise RuntimeError("Mismatching ReplicaContext.")
  raise RuntimeError(
      "Mismatching tf.distribute.Strategy objects: %s is not %s." %
      (context.strategy, replica_ctx.strategy))


def _require_strategy_scope_strategy(strategy):
  """Verify in a `strategy.scope()` in this thread."""
  context = _get_per_thread_mode()
  if context.strategy is strategy: return
  _wrong_strategy_scope(strategy, context)


def _require_strategy_scope_extended(extended):
  """Verify in a `distribution_strategy.scope()` in this thread."""
  context = _get_per_thread_mode()
  if context.strategy.extended is extended: return
  # Report error.
  strategy = extended._container_strategy()  # pylint: disable=protected-access
  _wrong_strategy_scope(strategy, context)


# ------------------------------------------------------------------------------
# Internal context managers used to implement the DistributionStrategy
# base class


class _CurrentDistributionContext(object):
  """Context manager setting the current `tf.distribute.Strategy`.

  Also: overrides the variable creator and optionally the current device.
  """

  def __init__(self,
               strategy,
               var_creator_scope,
               var_scope=None,
               default_device=None):
    self._context = distribution_strategy_context._CrossReplicaThreadMode(  # pylint: disable=protected-access
        strategy)
    self._var_creator_scope = var_creator_scope
    self._var_scope = var_scope
    if default_device:
      self._device_scope = ops.device(default_device)
    else:
      self._device_scope = None
    self._same_scope_again_count = 0

  def __enter__(self):
    # Allow this scope to be entered if this strategy is already in scope.
    if distribution_strategy_context.has_strategy():
      _require_cross_replica_or_default_context_extended(
          self._context.strategy.extended)
      self._same_scope_again_count += 1
    else:
      _push_per_thread_mode(self._context)
      if self._var_scope:
        self._var_scope.__enter__()
      self._var_creator_scope.__enter__()
      if self._device_scope:
        self._device_scope.__enter__()
    return self._context.strategy

  def __exit__(self, exception_type, exception_value, traceback):
    if self._same_scope_again_count > 0:
      self._same_scope_again_count -= 1
      return
    if self._device_scope:
      try:
        self._device_scope.__exit__(exception_type, exception_value, traceback)
      except RuntimeError as e:
        six.raise_from(
            RuntimeError("Device scope nesting error: move call to "
                         "tf.distribute.set_strategy() out of `with` scope."),
            e)

    try:
      self._var_creator_scope.__exit__(
          exception_type, exception_value, traceback)
    except RuntimeError as e:
      six.raise_from(
          RuntimeError("Variable creator scope nesting error: move call to "
                       "tf.distribute.set_strategy() out of `with` scope."),
          e)

    if self._var_scope:
      try:
        self._var_scope.__exit__(exception_type, exception_value, traceback)
      except RuntimeError as e:
        six.raise_from(
            RuntimeError("Variable scope nesting error: move call to "
                         "tf.distribute.set_strategy() out of `with` scope."),
            e)
    _pop_per_thread_mode()


# TODO(yuefengz): add more replication modes.
@tf_export("distribute.InputReplicationMode")
class InputReplicationMode(enum.Enum):
  """Replication mode for input function.

  * `PER_WORKER`: The input function will be called on each worker
    independently, creating as many input pipelines as number of workers.
    Replicas will dequeue from the local Dataset on their worker.
    `tf.distribute.Strategy` doesn't manage any state sharing between such
    separate input pipelines.
  """
  PER_WORKER = "PER_WORKER"


@tf_export("distribute.InputContext")
class InputContext(object):
  """A class wrapping information needed by an input function.

  This is a context class that is passed to the user's input fn and contains
  information about the compute replicas and input pipelines. The number of
  compute replicas (in sync training) helps compute per input pipeline batch
  size from the desired global batch size. Input pipeline information can be
  used to return a different subset of the input in each input pipeline (for
  e.g. shard the input pipeline, use a different input source etc).
  """

  def __init__(self,
               num_input_pipelines=1,
               input_pipeline_id=0,
               num_replicas_in_sync=1):
    """Initializes an InputContext object.

    Args:
      num_input_pipelines: the number of input pipelines in a cluster.
      input_pipeline_id: the current input pipeline id, should be an int in
        [0,`num_input_pipelines`).
      num_replicas_in_sync: the number of replicas that are in sync.
    """
    self._num_input_pipelines = num_input_pipelines
    self._input_pipeline_id = input_pipeline_id
    self._num_replicas_in_sync = num_replicas_in_sync

  @property
  def num_replicas_in_sync(self):
    """Returns the number of compute replicas in sync."""
    return self._num_replicas_in_sync

  @property
  def input_pipeline_id(self):
    """Returns the input pipeline ID."""
    return self._input_pipeline_id

  @property
  def num_input_pipelines(self):
    """Returns the number of input pipelines."""
    return self._num_input_pipelines

  def get_per_replica_batch_size(self, global_batch_size):
    """Returns the per-replica batch size.

    Args:
      global_batch_size: the global batch size which should be divisible by
        `num_replicas_in_sync`.

    Returns:
      the per-replica batch size.

    Raises:
      ValueError: if `global_batch_size` not divisible by
        `num_replicas_in_sync`.
    """
    if global_batch_size % self._num_replicas_in_sync != 0:
      raise ValueError("The `global_batch_size` %r is not divisible by "
                       "`num_replicas_in_sync` %r " %
                       (global_batch_size, self._num_replicas_in_sync))
    return global_batch_size // self._num_replicas_in_sync


# ------------------------------------------------------------------------------
# Base classes for all distribution strategies.


@tf_export("distribute.Strategy", v1=[])
class Strategy(object):
  """A list of devices with a state & compute distribution policy.

  See [the guide](https://www.tensorflow.org/alpha/guide/distribute_strategy)
  for overview and examples.
  """

  # TODO(josh11b): Raise an exception if variable partitioning requested before
  #   we add support.
  # TODO(josh11b): Also `parameter_device_index` property?
  # TODO(josh11b): `map()`
  # TODO(josh11b): ClusterSpec/ClusterResolver
  # TODO(josh11b): Partitioned computations, state; sharding
  # TODO(josh11b): Model parallelism: "replicas" with multiple devices; shuffling
  # TODO(josh11b): List of replicas with their worker and parameter devices
  #   (where the parameter devices may overlap in the ps case).

  def __init__(self, extended):
    self._extended = extended

    # Flag that is used to indicate whether distribution strategy is used with
    # Estimator. This is required for backward compatibility of loss scaling
    # when using v1 optimizer with estimator.
    self._scale_loss_for_estimator = False

  @property
  def extended(self):
    """`tf.distribute.StrategyExtended` with additional methods."""
    return self._extended

  @tf_contextlib.contextmanager
  def _scale_loss_for_estimator_enabled(self):
    """Scope which sets a flag used for scaling losses in optimizer.

    Yields:
      `_scale_loss_for_estimator_enabled` is a context manager with a
      side effect, but doesn't return a value.
    """
    self._scale_loss_for_estimator = True
    try:
      yield
    finally:
      self._scale_loss_for_estimator = False

  def scope(self):
    """Returns a context manager selecting this Strategy as current.

    Inside a `with strategy.scope():` code block, this thread
    will use a variable creator set by `strategy`, and will
    enter its "cross-replica context".

    Returns:
      A context manager.
    """
    return self._extended._scope(self)  # pylint: disable=protected-access

  @doc_controls.do_not_doc_inheritable  # DEPRECATED, moving to `extended`
  def colocate_vars_with(self, colocate_with_variable):
    """DEPRECATED: use extended.colocate_vars_with() instead."""
    return self._extended.colocate_vars_with(colocate_with_variable)

  @doc_controls.do_not_generate_docs  # DEPRECATED: TF 1.x only
  def make_dataset_iterator(self, dataset):
    """DEPRECATED TF 1.x ONLY."""
    return self._extended._make_dataset_iterator(dataset)  # pylint: disable=protected-access

  @doc_controls.do_not_generate_docs  # DEPRECATED: TF 1.x only
  def make_input_fn_iterator(self,
                             input_fn,
                             replication_mode=InputReplicationMode.PER_WORKER):
    """DEPRECATED TF 1.x ONLY."""
    if replication_mode != InputReplicationMode.PER_WORKER:
      raise ValueError(
          "Input replication mode not supported: %r" % replication_mode)
    with self.scope():
      return self.extended._make_input_fn_iterator(  # pylint: disable=protected-access
          input_fn, replication_mode=replication_mode)

  def experimental_make_numpy_dataset(self, numpy_input):
    """Makes a dataset for input provided via a numpy array.

    This avoids adding `numpy_input` as a large constant in the graph,
    and copies the data to the machine or machines that will be processing
    the input.

    Args:
      numpy_input: A nest of NumPy input arrays that will be distributed evenly
        across all replicas. Note that lists of Numpy arrays are stacked,
        as that is normal `tf.data.Dataset` behavior.

    Returns:
      A `tf.data.Dataset` representing `numpy_input`.
    """
    return self.extended.experimental_make_numpy_dataset(
        numpy_input, session=None)

  @doc_controls.do_not_generate_docs  # DEPRECATED: TF 1.x only
  def experimental_run(self, fn, input_iterator=None):
    """DEPRECATED TF 1.x ONLY."""
    with self.scope():
      args = (input_iterator.get_next(),) if input_iterator is not None else ()
    return self.experimental_run_v2(fn, args=args)

  def experimental_distribute_dataset(self, dataset):
    """Distributes a tf.data.Dataset instance provided via `dataset`.

    In a multi-worker setting, we will first attempt to distribute the dataset
    by attempting to detect whether the dataset is being created out of
    ReaderDatasets (e.g. TFRecordDataset, TextLineDataset, etc.) and if so,
    attempting to shard the input files. Note that there has to be at least one
    input file per worker. If you have less than one input file per worker, we
    suggest that you should disable distributing your dataset using the method
    below.

    If that attempt is unsuccessful (e.g. the dataset is created from a
    Dataset.range), we will shard the dataset evenly at the end by appending a
    `.shard` operation to the end of the processing pipeline. This will cause
    the entire preprocessing pipeline for all the data to be run on every
    worker, and each worker will do redundant work. We will print a warning
    if this method of sharding is selected.

    You can disable dataset distribution using the `auto_shard` option in
    `tf.data.experimental.DistributeOptions`.

    Within each host, we will also split the data among all the worker devices
    (if more than one a present), and this will happen even if multi-worker
    sharding is disabled using the method above.

    The following is an example:

    ```python
    strategy = tf.distribute.MirroredStrategy()

    # Create a dataset
    dataset = dataset_ops.Dataset.TFRecordDataset([
      "/a/1.tfr", "/a/2.tfr", "/a/3.tfr", /a/4.tfr"])

    # Distribute that dataset
    dist_dataset = strategy.experimental_distribute_dataset(dataset)
    # Iterate over the distributed dataset
    for x in dist_dataset:
      # process dataset elements
      strategy.experimental_run_v2(train_step, args=(x,))
    ```

    Args:
      dataset: `tf.data.Dataset` that will be sharded across all replicas using
        the rules stated above.

    Returns:
      A `DistributedDataset` which returns inputs for each step of the
      computation.
    """
    return self._extended._experimental_distribute_dataset(dataset)  # pylint: disable=protected-access

  def experimental_run_v2(self, fn, args=(), kwargs=None):
    """Runs ops in `fn` on each replica, with the given arguments.

    When eager execution is enabled, executes ops specified by `fn` on each
    replica. Otherwise, builds a graph to execute the ops on each replica.

    `fn` may call `tf.distribute.get_replica_context()` to access members such
    as `replica_id_in_sync_group`.

    IMPORTANT: Depending on the `tf.distribute.Strategy` implementation being
    used, and whether eager execution is enabled, `fn` may be called one or more
    times (once for each replica).

    Args:
      fn: The function to run. The output must be a `tf.nest` of `Tensor`s.
      args: (Optional) Positional arguments to `fn`.
      kwargs: (Optional) Keyword arguments to `fn`.

    Returns:
      Merged return value of `fn` across replicas. The structure of the return
      value is the same as the return value from `fn`. Each element in the
      structure can either be `PerReplica` (if the values are unsynchronized),
      `Mirrored` (if the values are kept in sync), or `Tensor` (if running on a
      single replica).
    """
    with self.scope():
      return self._extended.call_for_each_replica(fn, args=args, kwargs=kwargs)

  def reduce(self, reduce_op, value, axis):
    """Reduce `value` across replicas.

    Given a per-replica value returned by `experimental_run_v2`, say a
    per-example loss, the batch will be divided across all the replicas.  This
    function allows you to aggregate across replicas and optionally also across
    batch elements.  For example, if you have a global batch size of 8 and 2
    replicas, values for examples `[0, 1, 2, 3]` will be on replica 0 and
    `[4, 5, 6, 7]` will be on replica 1. By default, `reduce` will just
    aggregate across replicas, returning `[0+4, 1+5, 2+6, 3+7]`. This is useful
    when each replica is computing a scalar or some other value that doesn't
    have a "batch" dimension (like a gradient). More often you will want to
    aggregate across the global batch, which you can get by specifying the batch
    dimension as the `axis`, typically `axis=0`. In this case it would return a
    scalar `0+1+2+3+4+5+6+7`.

    If there is a last partial batch, you will need to specify an axis so
    that the resulting shape is consistent across replicas. So if the last
    batch has size 6 and it is divided into [0, 1, 2, 3] and [4, 5], you
    would get a shape mismatch unless you specify `axis=0`. If you specify
    `tf.distribute.ReduceOp.MEAN`, using `axis=0` will use the correct
    denominator of 6. Contrast this with computing `reduce_mean` to get a
    scalar value on each replica and this function to average those means,
    which will weigh some values `1/8` and others `1/4`.

    Args:
      reduce_op: A `tf.distribute.ReduceOp` value specifying how values should
        be combined.
      value: A "per replica" value, e.g. returned by `experimental_run_v2` to
        be combined into a single tensor.
      axis: Specifies the dimension to reduce along within each
        replica's tensor. Should typically be set to the batch dimension, or
        `None` to only reduce across replicas (e.g. if the tensor has no batch
        dimension).

    Returns:
      A `Tensor`.
    """
    # TODO(josh11b): support `value` being a nest.
    _require_cross_replica_or_default_context_extended(self._extended)
    if isinstance(reduce_op, six.string_types):
      reduce_op = reduce_util.ReduceOp(reduce_op.upper())
    if axis is None:
      return self._extended._reduce(reduce_op, value)  # pylint: disable=protected-access
    if reduce_op == reduce_util.ReduceOp.SUM:
      value = self.experimental_run_v2(
          lambda v: math_ops.reduce_sum(v, axis=axis), args=(value,))
      return self._extended._reduce(reduce_op, value)  # pylint: disable=protected-access
    if reduce_op != reduce_util.ReduceOp.MEAN:
      raise TypeError("Expected `reduce_op` to be a `tf.distribute.ReduceOp`, "
                      "not: %r" % reduce_op)
    # TODO(josh11b): Support list/tuple and tensor axis values.
    if not isinstance(axis, six.integer_types):
      raise TypeError("Expected `axis` to be an integer not: %r" % axis)

    def mean_reduce_helper(v, axis=axis):
      """Computes the numerator and denominator on each replica."""
      numer = math_ops.reduce_sum(v, axis=axis)
      if v.shape.rank is not None:
        # Note(joshl): We support axis < 0 to be consistent with the
        # tf.math.reduce_* operations.
        if axis < 0:
          if axis + v.shape.rank < 0:
            raise ValueError(
                "`axis` = %r out of range for `value` with rank %d" %
                (axis, v.shape.rank))
          axis += v.shape.rank
        elif axis >= v.shape.rank:
          raise ValueError(
              "`axis` = %r out of range for `value` with rank %d" %
              (axis, v.shape.rank))
        # TF v2 returns `None` for unknown dimensions and an integer for
        # known dimension, whereas TF v1 returns tensor_shape.Dimension(None)
        # or tensor_shape.Dimension(integer). `dimension_value` hides this
        # difference, always returning `None` or an integer.
        dim = tensor_shape.dimension_value(v.shape[axis])
        if dim is not None:
          # By returning a python value in the static shape case, we can
          # maybe get a fast path for reducing the denominator.
          return numer, dim
      elif axis < 0:
        axis = axis + array_ops.rank(v)
      denom = array_ops.shape_v2(v, out_type=dtypes.int64)[axis]
      # TODO(josh11b): Should we cast denom to v.dtype here instead of after the
      # reduce is complete?
      return numer, denom

    numer, denom = self.experimental_run_v2(mean_reduce_helper, args=(value,))
    # TODO(josh11b): Should batch reduce here instead of doing two.
    numer = self._extended._reduce(reduce_util.ReduceOp.SUM, numer)  # pylint: disable=protected-access
    denom = self._extended._reduce(reduce_util.ReduceOp.SUM, denom)  # pylint: disable=protected-access
    denom = math_ops.cast(denom, numer.dtype)
    return math_ops.truediv(numer, denom)

  @doc_controls.do_not_doc_inheritable  # DEPRECATED
  def unwrap(self, value):
    """Returns the list of all local per-replica values contained in `value`.

    DEPRECATED: Please use `experimental_local_results` instead.

    Note: This only returns values on the workers initiated by this client.
    When using a `Strategy` like
    `tf.distribute.experimental.MultiWorkerMirroredStrategy`, each worker
    will be its own client, and this function will only return values
    computed on that worker.

    Args:
      value: A value returned by `experimental_run()`,
        `extended.call_for_each_replica()`, or a variable created in `scope`.

    Returns:
      A tuple of values contained in `value`. If `value` represents a single
      value, this returns `(value,).`
    """
    return self._extended._local_results(value)  # pylint: disable=protected-access

  def experimental_local_results(self, value):
    """Returns the list of all local per-replica values contained in `value`.

    Note: This only returns values on the workers initiated by this client.
    When using a `Strategy` like
    `tf.distribute.experimental.MultiWorkerMirroredStrategy`, each worker
    will be its own client, and this function will only return values
    computed on that worker.

    Args:
      value: A value returned by `experimental_run()`, `experimental_run_v2()`,
        `extended.call_for_each_replica()`, or a variable created in `scope`.

    Returns:
      A tuple of values contained in `value`. If `value` represents a single
      value, this returns `(value,).`
    """
    return self._extended._local_results(value)  # pylint: disable=protected-access

  @doc_controls.do_not_doc_inheritable  # DEPRECATED: TF v1.x only
  def group(self, value, name=None):
    """Shortcut for `tf.group(self.experimental_local_results(value))`."""
    return self._extended._group(value, name)  # pylint: disable=protected-access

  @property
  def num_replicas_in_sync(self):
    """Returns number of replicas over which gradients are aggregated."""
    return self._extended._num_replicas_in_sync  # pylint: disable=protected-access

  @doc_controls.do_not_doc_inheritable  # DEPRECATED: see doc string
  def configure(self,
                session_config=None,
                cluster_spec=None,
                task_type=None,
                task_id=None):
    # pylint: disable=g-doc-return-or-yield,g-doc-args
    """DEPRECATED: use `update_config_proto` instead.

    Configures the strategy class.

    DEPRECATED: This method's functionality has been split into the strategy
    constructor and `update_config_proto`. In the future, we will allow passing
    cluster and config_proto to the constructor to configure the strategy. And
    `update_config_proto` can be used to update the config_proto based on the
    specific strategy.
    """
    return self._extended._configure(  # pylint: disable=protected-access
        session_config, cluster_spec, task_type, task_id)

  @doc_controls.do_not_generate_docs  # DEPRECATED
  def update_config_proto(self, config_proto):
    """DEPRECATED TF 1.x ONLY."""
    return self._extended._update_config_proto(config_proto)  # pylint: disable=protected-access

  def __deepcopy__(self, memo):
    # First do a regular deepcopy of `self`.
    cls = self.__class__
    result = cls.__new__(cls)
    memo[id(self)] = result
    for k, v in self.__dict__.items():
      setattr(result, k, copy.deepcopy(v, memo))
    # One little fix-up: we want `result._extended` to reference `result`
    # instead of `self`.
    result._extended._container_strategy_weakref = weakref.ref(result)  # pylint: disable=protected-access
    return result

  def __copy__(self):
    raise RuntimeError("Must only deepcopy DistributionStrategy.")


# TF v1.x version has additional deprecated APIs
@tf_export(v1=["distribute.Strategy"])
class StrategyV1(Strategy):
  """A list of devices with a state & compute distribution policy.

  See [the guide](https://www.tensorflow.org/guide/distribute_strategy)
  for overview and examples.
  """

  def make_dataset_iterator(self, dataset):
    """Makes an iterator for input provided via `dataset`.

    DEPRECATED: This method is not available in TF 2.x.

    Data from the given dataset will be distributed evenly across all the
    compute replicas. We will assume that the input dataset is batched by the
    global batch size. With this assumption, we will make a best effort to
    divide each batch across all the replicas (one or more workers).
    If this effort fails, an error will be thrown, and the user should instead
    use `make_input_fn_iterator` which provides more control to the user, and
    does not try to divide a batch across replicas.

    The user could also use `make_input_fn_iterator` if they want to
    customize which input is fed to which replica/worker etc.

    Args:
      dataset: `tf.data.Dataset` that will be distributed evenly across all
        replicas.

    Returns:
      An `tf.distribute.InputIterator` which returns inputs for each step of the
      computation.  User should call `initialize` on the returned iterator.
    """
    return self._extended._make_dataset_iterator(dataset)  # pylint: disable=protected-access

  def make_input_fn_iterator(self,  # pylint: disable=useless-super-delegation
                             input_fn,
                             replication_mode=InputReplicationMode.PER_WORKER):
    """Returns an iterator split across replicas created from an input function.

    DEPRECATED: This method is not available in TF 2.x.

    The `input_fn` should take an `tf.distribute.InputContext` object where
    information about batching and input sharding can be accessed:

    ```
    def input_fn(input_context):
      batch_size = input_context.get_per_replica_batch_size(global_batch_size)
      d = tf.data.Dataset.from_tensors([[1.]]).repeat().batch(batch_size)
      return d.shard(input_context.num_input_pipelines,
                     input_context.input_pipeline_id)
    with strategy.scope():
      iterator = strategy.make_input_fn_iterator(input_fn)
      replica_results = strategy.experimental_run(replica_fn, iterator)
    ```

    The `tf.data.Dataset` returned by `input_fn` should have a per-replica
    batch size, which may be computed using
    `input_context.get_per_replica_batch_size`.

    Args:
      input_fn: A function taking a `tf.distribute.InputContext` object and
        returning a `tf.data.Dataset`.
      replication_mode: an enum value of `tf.distribute.InputReplicationMode`.
        Only `PER_WORKER` is supported currently, which means there will be
        a single call to `input_fn` per worker. Replicas will dequeue from the
        local `tf.data.Dataset` on their worker.

    Returns:
      An iterator object that should first be `.initialize()`-ed. It may then
      either be passed to `strategy.experimental_run()` or you can
      `iterator.get_next()` to get the next value to pass to
      `strategy.extended.call_for_each_replica()`.
    """
    return super(StrategyV1, self).make_input_fn_iterator(
        input_fn, replication_mode)

  def experimental_make_numpy_dataset(self, numpy_input, session=None):
    """Makes a dataset for input provided via a numpy array.

    This avoids adding `numpy_input` as a large constant in the graph,
    and copies the data to the machine or machines that will be processing
    the input.

    Args:
      numpy_input: A nest of NumPy input arrays that will be distributed evenly
        across all replicas. Note that lists of Numpy arrays are stacked,
        as that is normal `tf.data.Dataset` behavior.
      session: (TensorFlow v1.x graph execution only) A session used for
        initialization.

    Returns:
      A `tf.data.Dataset` representing `numpy_input`.
    """
    return self.extended.experimental_make_numpy_dataset(
        numpy_input, session=session)

  def experimental_run(self, fn, input_iterator=None):  # pylint: disable=useless-super-delegation
    """Runs ops in `fn` on each replica, with inputs from `input_iterator`.

    DEPRECATED: This method is not available in TF 2.x. Please switch
    to using `experimental_run_v2` instead.

    When eager execution is enabled, executes ops specified by `fn` on each
    replica. Otherwise, builds a graph to execute the ops on each replica.

    Each replica will take a single, different input from the inputs provided by
    one `get_next` call on the input iterator.

    `fn` may call `tf.distribute.get_replica_context()` to access members such
    as `replica_id_in_sync_group`.

    IMPORTANT: Depending on the `tf.distribute.Strategy` implementation being
    used, and whether eager execution is enabled, `fn` may be called one or more
    times (once for each replica).

    Args:
      fn: The function to run. The inputs to the function must match the outputs
        of `input_iterator.get_next()`. The output must be a `tf.nest` of
        `Tensor`s.
      input_iterator: (Optional) input iterator from which the inputs are taken.

    Returns:
      Merged return value of `fn` across replicas. The structure of the return
      value is the same as the return value from `fn`. Each element in the
      structure can either be `PerReplica` (if the values are unsynchronized),
      `Mirrored` (if the values are kept in sync), or `Tensor` (if running on a
      single replica).
    """
    return super(StrategyV1, self).experimental_run(
        fn, input_iterator)

  def reduce(self, reduce_op, value, axis=None):
    return super(StrategyV1, self).reduce(reduce_op, value, axis)

  reduce.__doc__ = Strategy.reduce.__doc__

  def update_config_proto(self, config_proto):
    """Returns a copy of `config_proto` modified for use with this strategy.

    DEPRECATED: This method is not available in TF 2.x.

    The updated config has something needed to run a strategy, e.g.
    configuration to run collective ops, or device filters to improve
    distributed training performance.

    Args:
      config_proto: a `tf.ConfigProto` object.

    Returns:
      The updated copy of the `config_proto`.
    """
    return self._extended._update_config_proto(config_proto)  # pylint: disable=protected-access


# NOTE(josh11b): For any strategy that needs to support tf.compat.v1,
# instead descend from StrategyExtendedV1.
@tf_export("distribute.StrategyExtended", v1=[])
class StrategyExtendedV2(object):
  """Additional APIs for algorithms that need to be distribution-aware.

  The intent is that you can write an algorithm in a stylized way and
  it will be usable with a variety of different
  `tf.distribute.Strategy`
  implementations. Each descendant will implement a different strategy
  for distributing the algorithm across multiple devices/machines.
  Furthermore, these changes can be hidden inside the specific layers
  and other library classes that need special treatment to run in a
  distributed setting, so that most users' model definition code can
  run unchanged. The `tf.distribute.Strategy` API works the same way
  with eager and graph execution.

  First let's introduce a few high-level concepts:

  * _Data parallelism_ is where we run multiple copies of the model
    on different slices of the input data. This is in contrast to
    _model parallelism_ where we divide up a single copy of a model
    across multiple devices.
    Note: we only support data parallelism for now, but
    hope to add support for model parallelism in the future.
  * A _replica_ is one copy of the model, running on one slice of the
    input data.
  * _Synchronous_, or more commonly _sync_, training is where the
    updates from each replica are aggregated together before updating
    the model variables. This is in contrast to _asynchronous_, or
    _async_ training, where each replica updates the model variables
    independently.
  * Furthermore you might run your computation on multiple devices
    on one machine (or "host"), or on multiple machines/hosts.
    If you are running on multiple machines, you might have a
    single master host that drives computation across all of them,
    or you might have multiple clients driving the computation
    asynchronously.

  To distribute an algorithm, we might use some of these ingredients:

  * Parameter servers: These are hosts that hold a single copy of
    parameters/variables. All replicas that want to operate on a variable
    retrieve it at the beginning of a step and send an update to be
    applied at the end of the step. Can support either sync or async
    training.
  * Mirrored variables: These are variables that are copied to multiple
    devices, where we keep the copies in sync by applying the same
    updates to every copy. Normally would only be used with sync training.
  * Reductions and Allreduce: A _reduction_ is some method of
    aggregating multiple values into one value, like "sum" or
    "mean". If doing sync training, we will perform a reduction on the
    gradients to a parameter from all replicas before applying the
    update. Allreduce is an algorithm for performing a reduction on
    values from multiple devices and making the result available on
    all of those devices.
  * In the future we will have support for TensorFlow's partitioned
    variables, where a single variable is split across multiple
    devices.

  We have then a few approaches we want to support:

  * Code written (as if) with no knowledge of class `tf.distribute.Strategy`.
    This code should work as before, even if some of the layers, etc.
    used by that code are written to be distribution-aware. This is done
    by having a default `tf.distribute.Strategy` that gives ordinary behavior,
    and by default being in a single replica context.
  * Ordinary model code that you want to run using a specific
    `tf.distribute.Strategy`. This can be as simple as:

    ```
    with my_strategy.scope():
      iterator = my_strategy.make_dataset_iterator(dataset)
      session.run(iterator.initialize())
      replica_train_ops = my_strategy.experimental_run_v2(
          replica_fn, args=(iterator.get_next(),))
      train_op = my_strategy.group(replica_train_ops)
    ```

    This takes an ordinary `dataset` and `replica_fn` and runs it
    distributed using a particular `tf.distribute.Strategy` in
    `my_strategy`. Any variables created in `replica_fn` are created
    using `my_strategy`'s policy, and library functions called by
    `replica_fn` can use the `get_replica_context()` API to get enhanced
    behavior in this case.

  * If you want to write a distributed algorithm, you may use any of
    the `tf.distribute.Strategy` APIs inside a
    `with my_strategy.scope():` block of code.

  Lower-level concepts:

  * Wrapped values: In order to represent values parallel across devices
    (either replicas or the devices associated with a particular value), we
    wrap them in a "PerReplica" or "Mirrored" object that contains a map
    from device to values. "PerReplica" is used when the value may be
    different across replicas, and "Mirrored" when the value are the same.
  * Unwrapping and merging: Consider calling a function `fn` on multiple
    replicas, like `experimental_run_v2(fn, args=[w])` with an
    argument `w` that is a wrapped value. This means `w` will have a map taking
    replica device `d0` to `w0`, replica device `d1` to `w1`,
    etc. `experimental_run_v2()` unwraps `w` before calling `fn`, so
    it calls `fn(w0)` on `d0`, `fn(w1)` on `d1`, etc.  It then merges the return
    values from `fn()`, which can possibly result in wrapped values. For
    example, let's say `fn()` returns a tuple with three components: `(x, a,
    v0)` from replica 0, `(x, b, v1)` on replica 1, etc. If the first component
    is the same object `x` from every replica, then the first component of the
    merged result will also be `x`. If the second component is different (`a`,
    `b`, ...)  from each replica, then the merged value will have a wrapped map
    from replica device to the different values. If the third component is the
    members of a mirrored variable (`v` maps `d0` to `v0`, `d1` to `v1`, etc.),
    then the merged result will be that mirrored variable (`v`).
  * Replica context vs. Cross-replica context: _replica context_ is when we
    are in some function that is being called once for each replica.
    Otherwise we are in cross-replica context, which is useful for
    calling `tf.distribute.Strategy` methods which operate across the
    replicas (like `reduce_to()`). By default you start in a replica context
    (the default "single replica context") and then some methods can
    switch you back and forth, as described below.
  * Worker devices vs. parameter devices: Most replica computations will
    happen on worker devices. Since we don't yet support model
    parallelism, there will be one worker device per replica. When using
    parameter servers (see above), the set of devices holding
    variables may be different, otherwise the parameter devices might
    match the worker devices.
  * Non-slot devices are some subset of the parameter devices where we
    put all the non-slot variables. We need to ensure that all
    non-slot variables are allocated on the same device, or mirrored
    across the same set of devices. If you have some variable you want
    to colocate all the non-slot variables with, you can use
    `colocate_vars_with()` to get the remaining non-slot variables on
    the same device.  Otherwise you can use `non_slot_devices()` to
    pick a consistent set of devices to pass to both
    `colocate_vars_with()` and `update_non_slot()`.

  When using a `tf.distribute.Strategy`, we have a new type dimension
  called _locality_ that says what values are compatible with which
  APIs:

  * T: different value for each replica (e.g. a PerReplica-wrapped value).
  * M: value is "mirrored" across replicas, i.e. there are copies with the
    same value on each replica (e.g. a Mirrored-wrapped value).
  * V(`v`): value is "mirrored" across all the devices which have a
    copy of variable `v` (also a Mirrored-wrapped value, but over
    parameter devices instead of worker devices).
  * N: value is "mirrored" across all the "non-slot" devices

  Rules for methods with respect to locality and single-replica vs.
  cross-replica context:

  * `with d.scope()`: default single-replica context -> cross-replica context
    for `d`
  * `with d.extended.colocate_vars_with(v)`: in replica/cross-replica context,
    variables will be created with locality V(`v`). That is, if we write
    `with d.extended.colocate_vars_with(v1):
    v2 = tf.Variable(...)`, then `v2` will have locality V(`v1`),
    i.e. locality V(`v2`) will equal V(`v1`).
  * `with d.extended.colocate_vars_with(d.extended.non_slot_devices(...))`: in
    replica/cross-replica context, variables will be created with locality N
  * `v = tf.Variable(...)`: in replica/cross-replica context,
    creates a variable (which by definition will have locality V(`v`), though
    will match another locality if inside a `colocate_vars_with`
    scope).
  * `d.make_dataset_iterator(dataset)`: in cross-replica
    context, produces an iterator with locality T
  * `d.experimental_run_v2(fn, ...)`: in cross-replica context, runs
    `fn()` in a replica context (and so may call `get_replica_context()` and
    use its API, including `merge_call()` to get back to cross-replica
    context), once for each replica. May use values with locality T or
    M, and any variable.
  * `d.extended.reduce_to(m, t, t)`: in cross-replica context, accepts t with
    locality T and produces a value with locality M.
  * `d.extended.reduce_to(m, t, v)`: in cross-replica context, accepts t with
    locality T and produces a value with locality V(`v`).
  * `d.extended.batch_reduce_to(m, [(t, v)]): see `d.extended.reduce_to()`
  * `d.extended.update(v, fn, ...)`: in cross-replica context, runs `fn()` once
    for each device `v` is copied to, all inputs should have locality
    V(`v`), output will have locality V(`v`) as well.
  * `d.extended.update_non_slot(d.extended.non_slot_devices(), fn)`: in
    cross-replica context, like `d.extended.update()` except with locality N.

  The standard pattern for updating variables is to:

  1. Create an input iterator with `d.make_dataset_iterator()`.
  2. Define each replica `d.experimental_run_v2()` up to the point of
     getting a list of gradient, variable pairs.
  3. Call `d.extended.reduce_to(VariableAggregation.SUM, t, v)` or
     `d.extended.batch_reduce_to()` to sum the gradients (with locality T)
     into values with locality V(`v`).
  4. Call `d.extended.update(v)` for each variable to update its value.

  Steps 3 and 4 are done automatically by class `Optimizer` if you call
  its `apply_gradients` method in a replica context. Otherwise you can
  manually call its `_distributed_apply` method in a cross-replica context.

  Another thing you might want to do in the middle of your replica function is
  an all-reduce of some intermediate value, using `d.extended.reduce_to()` or
  `d.extended.batch_reduce_to()`. You simply provide the same tensor as the
  input and destination.

  Layers should expect to be called in a replica context, and can use
  the `tf.distribute.get_replica_context` function to get a
  `tf.distribute.ReplicaContext` object. The
  `ReplicaContext` object has a `merge_call()` method for entering
  cross-replica context where you can use `reduce_to()` (or
  `batch_reduce_to()`) and then optionally `update()` to update state.

  You may use this API whether or not a `tf.distribute.Strategy` is
  being used, since there is a default implementation of
  `ReplicaContext` and `tf.distribute.Strategy`.

  NOTE for new `tf.distribute.Strategy` implementations: Please put all logic
  in a subclass of `tf.distribute.StrategyExtended`. The only code needed for
  the `tf.distribute.Strategy` subclass is for instantiating your subclass of
  `tf.distribute.StrategyExtended` in the `__init__` method.
  """

  def __init__(self, container_strategy):
    self._container_strategy_weakref = weakref.ref(container_strategy)
    self._default_device = None
    # This property is used to determine if we should set drop_remainder=True
    # when creating Datasets from numpy array inputs.
    self._require_static_shapes = False

  def _container_strategy(self):
    """Get the containing `tf.distribute.Strategy`.

    This should not generally be needed except when creating a new
    `ReplicaContext` and to validate that the caller is in the correct
    `scope()`.

    Returns:
      The `tf.distribute.Strategy` such that `strategy.extended` is `self`.
    """
    container_strategy = self._container_strategy_weakref()
    assert container_strategy is not None
    return container_strategy

  def _scope(self, strategy):
    """Implementation of tf.distribute.Strategy.scope()."""
    def creator_with_resource_vars(*args, **kwargs):
      _require_strategy_scope_extended(self)
      kwargs["use_resource"] = True
      kwargs["distribute_strategy"] = strategy
      return self._create_variable(*args, **kwargs)

    def distributed_getter(getter, *args, **kwargs):
      if not self._allow_variable_partition():
        if kwargs.pop("partitioner", None) is not None:
          tf_logging.log_first_n(
              tf_logging.WARN, "Partitioned variables are disabled when using "
              "current tf.distribute.Strategy.", 1)
      return getter(*args, **kwargs)

    return _CurrentDistributionContext(
        strategy,
        variable_scope.variable_creator_scope(creator_with_resource_vars),
        variable_scope.variable_scope(
            variable_scope.get_variable_scope(),
            custom_getter=distributed_getter), self._default_device)

  def _allow_variable_partition(self):
    return False

  def _create_variable(self, next_creator, *args, **kwargs):
    # Note: should support "colocate_with" argument.
    raise NotImplementedError("must be implemented in descendants")

  def variable_created_in_scope(self, v):
    """Tests whether `v` was created while this strategy scope was active.

    Variables created inside the strategy scope are "owned" by it:

    >>> with strategy.scope():
    ...   v = tf.Variable(1.)
    >>> strategy.variable_created_in_scope(v)
    True

    Variables created outside the strategy are not owned by it:

    >>> v = tf.Variable(1.)
    >>> strategy.variable_created_in_scope(v)
    False

    Args:
      v: A `tf.Variable` instance.

    Returns:
      True if `v` was created inside the scope, False if not.
    """
    return v._distribute_strategy == self._container_strategy_weakref()  # pylint: disable=protected-access

  def colocate_vars_with(self, colocate_with_variable):
    """Scope that controls which devices variables will be created on.

    No operations should be added to the graph inside this scope, it
    should only be used when creating variables (some implementations
    work by changing variable creation, others work by using a
    tf.compat.v1.colocate_with() scope).

    This may only be used inside `self.scope()`.

    Example usage:

    ```
    with strategy.scope():
      var1 = tf.Variable(...)
      with strategy.extended.colocate_vars_with(var1):
        # var2 and var3 will be created on the same device(s) as var1
        var2 = tf.Variable(...)
        var3 = tf.Variable(...)

      def fn(v1, v2, v3):
        # operates on v1 from var1, v2 from var2, and v3 from var3

      # `fn` runs on every device `var1` is on, `var2` and `var3` will be there
      # too.
      strategy.extended.update(var1, fn, args=(var2, var3))
    ```

    Args:
      colocate_with_variable: A variable created in this strategy's `scope()`.
        Variables created while in the returned context manager will be on the
        same set of devices as `colocate_with_variable`.

    Returns:
      A context manager.
    """
    def create_colocated_variable(next_creator, *args, **kwargs):
      _require_strategy_scope_extended(self)
      kwargs["use_resource"] = True
      kwargs["colocate_with"] = colocate_with_variable
      return next_creator(*args, **kwargs)

    _require_strategy_scope_extended(self)
    self._validate_colocate_with_variable(colocate_with_variable)
    return variable_scope.variable_creator_scope(create_colocated_variable)

  def _validate_colocate_with_variable(self, colocate_with_variable):
    """Validate `colocate_with_variable` argument to `colocate_vars_with`."""
    pass

  def _make_dataset_iterator(self, dataset):
    raise NotImplementedError("must be implemented in descendants")

  def _make_input_fn_iterator(self, input_fn, replication_mode):
    raise NotImplementedError("must be implemented in descendants")

  def _experimental_distribute_dataset(self, dataset):
    raise NotImplementedError("must be implemented in descendants")

  def _reduce(self, reduce_op, value):
    # Default implementation until we have an implementation for each strategy.
    return self._local_results(
        self._reduce_to(reduce_op, value,
                        device_util.current() or "/device:CPU:0"))[0]

  def reduce_to(self, reduce_op, value, destinations):
    """Combine (via e.g. sum or mean) values across replicas.

    Args:
      reduce_op: Reduction type, an instance of `tf.distribute.ReduceOp` enum.
      value: A per-replica value with one value per replica.
      destinations: A mirrored variable, a per-replica tensor, or a device
        string. The return value will be copied to all destination devices (or
        all the devices where the `destinations` value resides). To perform an
        all-reduction, pass `value` to `destinations`.

    Returns:
      A value mirrored to `destinations`.
    """
    # TODO(josh11b): More docstring
    _require_cross_replica_or_default_context_extended(self)
    assert not isinstance(destinations, (list, tuple))
    assert not isinstance(reduce_op, variable_scope.VariableAggregation)
    if isinstance(reduce_op, six.string_types):
      reduce_op = reduce_util.ReduceOp(reduce_op.upper())
    assert (reduce_op == reduce_util.ReduceOp.SUM or
            reduce_op == reduce_util.ReduceOp.MEAN)
    return self._reduce_to(reduce_op, value, destinations)

  def _reduce_to(self, reduce_op, value, destinations):
    raise NotImplementedError("must be implemented in descendants")

  def batch_reduce_to(self, reduce_op, value_destination_pairs):
    """Combine multiple `reduce_to` calls into one for faster execution.

    Args:
      reduce_op: Reduction type, an instance of `tf.distribute.ReduceOp` enum.
      value_destination_pairs: A sequence of (value, destinations)
        pairs. See `reduce_to()` for a description.

    Returns:
      A list of mirrored values, one per pair in `value_destination_pairs`.
    """
    # TODO(josh11b): More docstring
    _require_cross_replica_or_default_context_extended(self)
    assert not isinstance(reduce_op, variable_scope.VariableAggregation)
    if isinstance(reduce_op, six.string_types):
      reduce_op = reduce_util.ReduceOp(reduce_op.upper())
    return self._batch_reduce_to(reduce_op, value_destination_pairs)

  def _batch_reduce_to(self, reduce_op, value_destination_pairs):
    return [
        self.reduce_to(reduce_op, t, destinations=v)
        for t, v in value_destination_pairs
    ]

  def update(self, var, fn, args=(), kwargs=None, group=True):
    """Run `fn` to update `var` using inputs mirrored to the same devices.

    If `var` is mirrored across multiple devices, then this implements
    logic like:

    ```
    results = {}
    for device, v in var:
      with tf.device(device):
        # args and kwargs will be unwrapped if they are mirrored.
        results[device] = fn(v, *args, **kwargs)
    return merged(results)
    ```

    Otherwise this returns `fn(var, *args, **kwargs)` colocated with `var`.

    Neither `args` nor `kwargs` may contain per-replica values.
    If they contain mirrored values, they will be unwrapped before
    calling `fn`.

    Args:
      var: Variable, possibly mirrored to multiple devices, to operate on.
      fn: Function to call. Should take the variable as the first argument.
      args: Tuple or list. Additional positional arguments to pass to `fn()`.
      kwargs: Dict with keyword arguments to pass to `fn()`.
      group: Boolean. Defaults to True. If False, the return value will be
        unwrapped.

    Returns:
      By default, the merged return value of `fn` across all replicas.  The
      merged result has dependencies to make sure that if it is evaluated at
      all, the side effects (updates) will happen on every replica. If instead
      "group=False" is specified, this function will return a nest of lists
      where each list has an element per replica, and the caller is responsible
      for ensuring all elements are executed.
    """
    _require_cross_replica_or_default_context_extended(self)
    if kwargs is None:
      kwargs = {}
    with self._container_strategy().scope():
      return self._update(var, fn, args, kwargs, group)

  def _update(self, var, fn, args, kwargs, group):
    raise NotImplementedError("must be implemented in descendants")

  def update_non_slot(
      self, colocate_with, fn, args=(), kwargs=None, group=True):
    """Runs `fn(*args, **kwargs)` on `colocate_with` devices.

    Args:
      colocate_with: The return value of `non_slot_devices()`.
      fn: Function to execute.
      args: Tuple or list. Positional arguments to pass to `fn()`.
      kwargs: Dict with keyword arguments to pass to `fn()`.
      group: Boolean. Defaults to True. If False, the return value will be
        unwrapped.

    Returns:
      Return value of `fn`, possibly merged across devices.
    """
    _require_cross_replica_or_default_context_extended(self)
    if kwargs is None:
      kwargs = {}
    with self._container_strategy().scope():
      return self._update_non_slot(colocate_with, fn, args, kwargs, group)

  def _update_non_slot(self, colocate_with, fn, args, kwargs, group):
    raise NotImplementedError("must be implemented in descendants")

  def _local_results(self, distributed_value):
    raise NotImplementedError("must be implemented in descendants")

  def value_container(self, value):
    """Returns the container that this per-replica `value` belongs to.

    Args:
      value: A value returned by `experimental_run_v2()` or a variable
        created in `scope()`.

    Returns:
      A container that `value` belongs to.
      If value does not belong to any container (including the case of
      container having been destroyed), returns the value itself.
      `value in experimental_local_results(value_container(value))` will
      always be true.
    """
    raise NotImplementedError("must be implemented in descendants")

  def _group(self, value, name=None):
    """Implementation of `group`."""
    value = nest.flatten(self._local_results(value))

    if len(value) != 1 or name is not None:
      return control_flow_ops.group(value, name=name)
    # Special handling for the common case of one op.
    v, = value
    if hasattr(v, "op"):
      v = v.op
    return v

  @property
  def experimental_require_static_shapes(self):
    return self._require_static_shapes

  @property
  def _num_replicas_in_sync(self):
    """Returns number of replicas over which gradients are aggregated."""
    raise NotImplementedError("must be implemented in descendants")

  @property
  def worker_devices(self):
    """Returns the tuple of all devices used to for compute replica execution.
    """
    # TODO(josh11b): More docstring
    raise NotImplementedError("must be implemented in descendants")

  @property
  def parameter_devices(self):
    """Returns the tuple of all devices used to place variables."""
    # TODO(josh11b): More docstring
    raise NotImplementedError("must be implemented in descendants")

  def non_slot_devices(self, var_list):
    """Device(s) for non-slot variables.

    Create variables on these devices in a
    `with colocate_vars_with(non_slot_devices(...)):` block.
    Update those using `update_non_slot()`.

    Args:
      var_list: The list of variables being optimized, needed with the
        default `tf.distribute.Strategy`.
    """
    raise NotImplementedError("must be implemented in descendants")

  def _configure(self,
                 session_config=None,
                 cluster_spec=None,
                 task_type=None,
                 task_id=None):
    """Configures the strategy class."""
    del session_config, cluster_spec, task_type, task_id

  def _update_config_proto(self, config_proto):
    return copy.deepcopy(config_proto)


@tf_export(v1=["distribute.StrategyExtended"])  # pylint: disable=missing-docstring
class StrategyExtendedV1(StrategyExtendedV2):

  __doc__ = StrategyExtendedV2.__doc__

  def experimental_make_numpy_dataset(self, numpy_input, session=None):
    """Makes a dataset for input provided via a numpy array.

    This avoids adding `numpy_input` as a large constant in the graph,
    and copies the data to the machine or machines that will be processing
    the input.

    Args:
      numpy_input: A nest of NumPy input arrays that will be distributed evenly
        across all replicas. Note that lists of Numpy arrays are stacked, as
        that is normal `tf.data.Dataset` behavior.
      session: (TensorFlow v1.x graph execution only) A session used for
        initialization.

    Returns:
      A `tf.data.Dataset` representing `numpy_input`.
    """
    _require_cross_replica_or_default_context_extended(self)
    return self._experimental_make_numpy_dataset(numpy_input, session=session)

  def _experimental_make_numpy_dataset(self, numpy_input, session):
    raise NotImplementedError("must be implemented in descendants")

  def broadcast_to(self, tensor, destinations):
    """Mirror a tensor on one device to all worker devices.

    Args:
      tensor: A Tensor value to broadcast.
      destinations: A mirrored variable or device string specifying the
        destination devices to copy `tensor` to.

    Returns:
      A value mirrored to `destinations` devices.
    """
    assert destinations is not None  # from old strategy.broadcast()
    # TODO(josh11b): More docstring
    _require_cross_replica_or_default_context_extended(self)
    assert not isinstance(destinations, (list, tuple))
    return self._broadcast_to(tensor, destinations)

  def _broadcast_to(self, tensor, destinations):
    raise NotImplementedError("must be implemented in descendants")

  def experimental_run_steps_on_iterator(self,
                                         fn,
                                         iterator,
                                         iterations=1,
                                         initial_loop_values=None):
    """Run `fn` with input from `iterator` for `iterations` times.

    This method can be used to run a step function for training a number of
    times using input from a dataset.

    Args:
      fn: function to run using this distribution strategy. The function must
        have the following signature: `def fn(context, inputs)`. `context` is an
          instance of `MultiStepContext` that will be passed when `fn` is run.
          `context` can be used to specify the outputs to be returned from `fn`
          by calling `context.set_last_step_output`. It can also be used to
          capture non tensor outputs by `context.set_non_tensor_output`. See
          `MultiStepContext` documentation for more information. `inputs` will
          have same type/structure as `iterator.get_next()`. Typically, `fn`
          will use `call_for_each_replica` method of the strategy to distribute
          the computation over multiple replicas.
      iterator: Iterator of a dataset that represents the input for `fn`. The
        caller is responsible for initializing the iterator as needed.
      iterations: (Optional) Number of iterations that `fn` should be run.
        Defaults to 1.
      initial_loop_values: (Optional) Initial values to be passed into the
        loop that runs `fn`. Defaults to `None`. # TODO(priyag): Remove
          initial_loop_values argument when we have a mechanism to infer the
          outputs of `fn`.

    Returns:
      Returns the `MultiStepContext` object which has the following properties,
      among other things:
        - run_op: An op that runs `fn` `iterations` times.
        - last_step_outputs: A dictionary containing tensors set using
        `context.set_last_step_output`. Evaluating this returns the value of
        the tensors after the last iteration.
        - non_tensor_outputs: A dictionatry containing anything that was set by
          `fn` by calling `context.set_non_tensor_output`.
    """
    _require_cross_replica_or_default_context_extended(self)
    with self._container_strategy().scope():
      return self._experimental_run_steps_on_iterator(fn, iterator, iterations,
                                                      initial_loop_values)

  def _experimental_run_steps_on_iterator(self, fn, iterator, iterations,
                                          initial_loop_values):
    raise NotImplementedError("must be implemented in descendants")

  def call_for_each_replica(self, fn, args=(), kwargs=None):
    """Run `fn` once per replica.

    `fn` may call `tf.get_replica_context()` to access methods such as
    `replica_id_in_sync_group` and `merge_call()`.

    `merge_call()` is used to communicate between the replicas and
    re-enter the cross-replica context. All replicas pause their execution
    having encountered a `merge_call()` call. After that the
    `merge_fn`-function is executed. Its results are then unwrapped and
    given back to each replica call. After that execution resumes until
    `fn` is complete or encounters another `merge_call()`.  Example:

    ```python
    # Called once in "cross-replica" context.
    def merge_fn(distribution, three_plus_replica_id):
      # sum the values across replicas
      return sum(distribution.experimental_local_results(three_plus_replica_id))

    # Called once per replica in `distribution`, in a "replica" context.
    def fn(three):
      replica_ctx = tf.get_replica_context()
      v = three + replica_ctx.replica_id_in_sync_group
      # Computes the sum of the `v` values across all replicas.
      s = replica_ctx.merge_call(merge_fn, args=(v,))
      return s + v

    with distribution.scope():
      # in "cross-replica" context
      ...
      merged_results = distribution.experimental_run_v2(fn, args=[3])
      # merged_results has the values from every replica execution of `fn`.
      # This statement prints a list:
      print(distribution.experimental_local_results(merged_results))
    ```

    Args:
      fn: function to run (will be run once per replica).
      args: Tuple or list with positional arguments for `fn`.
      kwargs: Dict with keyword arguments for `fn`.

    Returns:
      Merged return value of `fn` across all replicas.
    """
    _require_cross_replica_or_default_context_extended(self)
    if kwargs is None:
      kwargs = {}
    with self._container_strategy().scope():
      return self._call_for_each_replica(fn, args, kwargs)

  def _call_for_each_replica(self, fn, args, kwargs):
    raise NotImplementedError("must be implemented in descendants")

  def read_var(self, v):
    """Reads the value of a variable.

    Returns the aggregate value of a replica-local variable, or the
    (read-only) value of any other variable.

    Args:
      v: A variable allocated within the scope of this `tf.distribute.Strategy`.

    Returns:
      A tensor representing the value of `v`, aggregated across replicas if
      necessary.
    """
    raise NotImplementedError("must be implemented in descendants")

  @property
  def experimental_between_graph(self):
    """Whether the strategy uses between-graph replication or not.

      This is expected to return a constant value that will not be changed
      throughout its life cycle.
    """
    raise NotImplementedError("must be implemented in descendants")

  @property
  def experimental_should_init(self):
    """Whether initialization is needed."""
    raise NotImplementedError("must be implemented in descendants")

  @property
  def should_checkpoint(self):
    """Whether checkpointing is needed."""
    raise NotImplementedError("must be implemented in descendants")

  @property
  def should_save_summary(self):
    """Whether saving summaries is needed."""
    raise NotImplementedError("must be implemented in descendants")


# A note about the difference between the context managers
# `ReplicaContext` (defined here) and `_CurrentDistributionContext`
# (defined above) used by `tf.distribute.Strategy.scope()`:
#
# * a ReplicaContext is only present during a `experimental_run_v2()`
#   call (except during a `merge_run` call) and in such a scope it
#   will be returned by calls to `get_replica_context()`.  Implementers of new
#   Strategy descendants will frequently also need to
#   define a descendant of ReplicaContext, and are responsible for
#   entering and exiting this context.
#
# * Strategy.scope() sets up a variable_creator scope that
#   changes variable creation calls (e.g. to make mirrored
#   variables). This is intended as an outer scope that users enter once
#   around their model creation and graph definition. There is no
#   anticipated need to define descendants of _CurrentDistributionContext.
#   It sets the current Strategy for purposes of
#   `get_strategy()` and `has_strategy()`
#   and switches the thread mode to a "cross-replica context".
@tf_export("distribute.ReplicaContext")
class ReplicaContext(object):
  """`tf.distribute.Strategy` API when in a replica context.

  To be used inside your replicated step function, such as in a
  `tf.distribute.Strategy.experimental_run_v2` call.
  """

  def __init__(self, strategy, replica_id_in_sync_group):
    self._strategy = strategy
    self._thread_context = distribution_strategy_context._InReplicaThreadMode(  # pylint: disable=protected-access
        self)
    self._replica_id_in_sync_group = replica_id_in_sync_group
    self._summary_recording_distribution_strategy = None

  def __enter__(self):
    _push_per_thread_mode(self._thread_context)
    ctx = eager_context.context()

    def replica_id_is_zero():
      return math_ops.equal(self._replica_id_in_sync_group,
                            constant_op.constant(0))

    self._summary_recording_distribution_strategy = (
        ctx.summary_recording_distribution_strategy)
    ctx.summary_recording_distribution_strategy = replica_id_is_zero

  def __exit__(self, exception_type, exception_value, traceback):
    ctx = eager_context.context()
    ctx.summary_recording_distribution_strategy = (
        self._summary_recording_distribution_strategy)
    _pop_per_thread_mode()

  def merge_call(self, merge_fn, args=(), kwargs=None):
    """Merge args across replicas and run `merge_fn` in a cross-replica context.

    This allows communication and coordination when there are multiple calls
    to a model function triggered by a call to
    `strategy.experimental_run_v2(model_fn, ...)`.

    See `tf.distribute.Strategy.experimental_run_v2` for an
    explanation.

    If not inside a distributed scope, this is equivalent to:

    ```
    strategy = tf.distribute.get_strategy()
    with cross-replica-context(strategy):
      return merge_fn(strategy, *args, **kwargs)
    ```

    Args:
      merge_fn: function that joins arguments from threads that are given as
        PerReplica. It accepts `tf.distribute.Strategy` object as
        the first argument.
      args: List or tuple with positional per-thread arguments for `merge_fn`.
      kwargs: Dict with keyword per-thread arguments for `merge_fn`.

    Returns:
      The return value of `merge_fn`, except for `PerReplica` values which are
      unpacked.
    """
    require_replica_context(self)
    if kwargs is None:
      kwargs = {}
    return self._merge_call(merge_fn, args, kwargs)

  def _merge_call(self, merge_fn, args, kwargs):
    """Default implementation for single replica."""
    _push_per_thread_mode(  # thread-local, so not needed with multiple threads
        distribution_strategy_context._CrossReplicaThreadMode(self._strategy))  # pylint: disable=protected-access
    try:
      return merge_fn(self._strategy, *args, **kwargs)
    finally:
      _pop_per_thread_mode()

  @property
  def num_replicas_in_sync(self):
    """Returns number of replicas over which gradients are aggregated."""
    return self._strategy.num_replicas_in_sync

  @property
  def replica_id_in_sync_group(self):
    """Which replica is being defined, from 0 to `num_replicas_in_sync - 1`."""
    require_replica_context(self)
    return self._replica_id_in_sync_group

  @property
  def strategy(self):
    """The current `tf.distribute.Strategy` object."""
    return self._strategy

  @property
  def devices(self):
    """The devices this replica is to be executed on, as a tuple of strings."""
    require_replica_context(self)
    return (device_util.current(),)

  def all_reduce(self, reduce_op, value):
    """All-reduces the given `Tensor` nest across replicas.

    If `all_reduce` is called in any replica, it must be called in all replicas.
    The nested structure and `Tensor` shapes must be identical in all replicas.

    IMPORTANT: The ordering of communications must be identical in all replicas.

    Example with two replicas:
      Replica 0 `value`: {'a': 1, 'b': [40,  1]}
      Replica 1 `value`: {'a': 3, 'b': [ 2, 98]}

      If `reduce_op` == `SUM`:
        Result (on all replicas): {'a': 4, 'b': [42, 99]}

      If `reduce_op` == `MEAN`:
        Result (on all replicas): {'a': 2, 'b': [21, 49.5]}

    Args:
      reduce_op: Reduction type, an instance of `tf.distribute.ReduceOp` enum.
      value: The nested structure of `Tensor`s to all-reduced.
        The structure must be compatible with `tf.nest`.

    Returns:
       A `Tensor` nest with the reduced `value`s from each replica.
    """
    def batch_all_reduce(strategy, *value_flat):
      return strategy.extended.batch_reduce_to(
          reduce_op, [(v, _batch_reduce_destination(v)) for v in value_flat])

    if reduce_op in [reduce_util.ReduceOp.SUM, reduce_util.ReduceOp.MEAN]:
      # TODO(cjfj): Work out why `batch_reduce` doesn't return the correct grad.
      @custom_gradient.custom_gradient
      def grad_wrapper(*xs):
        ys = self.merge_call(batch_all_reduce, args=xs)
        # The gradient of an all-sum is itself an all-sum (all-mean, likewise).
        return ys, lambda *dy_s: self.all_reduce(reduce_op, dy_s)
      return nest.pack_sequence_as(value, grad_wrapper(*nest.flatten(value)))
    else:
      # TODO(cjfj): Implement gradients for other reductions.
      reduced = nest.pack_sequence_as(
          value, self.merge_call(batch_all_reduce, args=nest.flatten(value)))
      return nest.map_structure(array_ops.prevent_gradient, reduced)

  # TODO(josh11b): Implement `start_all_reduce(method, t)` for efficient
  # all-reduce. It would return a function returning the result of reducing `t`
  # across all replicas. The caller would wait to call this function until they
  # needed the reduce result, allowing an efficient implementation:
  # * With eager execution, the reduction could be performed asynchronously
  #   in the background, not blocking until the result was needed.
  # * When constructing a graph, it could batch up all reduction requests up
  #   to that point that the first result is needed. Most likely this can be
  #   implemented in terms of `merge_call()` and `batch_reduce_to()`.


def _batch_reduce_destination(x):
  """Returns the destinations for batch all-reduce."""
  if isinstance(x, ops.Tensor):  # One device strategies.
    return x.device
  else:
    return x


# ------------------------------------------------------------------------------


class _DefaultDistributionStrategy(StrategyV1):
  """Default `tf.distribute.Strategy` if none is explicitly selected."""

  def __init__(self):
    super(_DefaultDistributionStrategy, self).__init__(
        _DefaultDistributionExtended(self))


class _DefaultDistributionExtended(StrategyExtendedV1):
  """Implementation of _DefaultDistributionStrategy."""

  def _scope(self, strategy):
    """Context manager setting a variable creator and `self` as current."""
    if distribution_strategy_context.has_strategy():
      raise RuntimeError("Must not nest tf.distribute.Strategy scopes.")

    def creator(next_creator, *args, **kwargs):
      _require_strategy_scope_strategy(strategy)
      return next_creator(*args, **kwargs)

    return _CurrentDistributionContext(
        strategy, variable_scope.variable_creator_scope(creator))

  def colocate_vars_with(self, colocate_with_variable):
    """Does not require `self.scope`."""
    _require_strategy_scope_extended(self)
    return ops.colocate_with(colocate_with_variable)

  def variable_created_in_scope(self, v):
    return v._distribute_strategy is None  # pylint: disable=protected-access

  def _experimental_distribute_dataset(self, dataset):
    return dataset

  def _make_dataset_iterator(self, dataset):
    return _DefaultDistributionExtended.DefaultInputIterator(dataset)

  def _make_input_fn_iterator(self,
                              input_fn,
                              replication_mode=InputReplicationMode.PER_WORKER):
    dataset = input_fn(InputContext())
    return _DefaultDistributionExtended.DefaultInputIterator(dataset)

  def _experimental_make_numpy_dataset(self, numpy_input, session):
    numpy_flat = nest.flatten(numpy_input)
    vars_flat = tuple(
        variable_scope.variable(array_ops.zeros(i.shape, i.dtype),
                                trainable=False, use_resource=True)
        for i in numpy_flat
    )
    for v, i in zip(vars_flat, numpy_flat):
      numpy_dataset.init_var_from_numpy(v, i, session)
    vars_nested = nest.pack_sequence_as(numpy_input, vars_flat)
    return dataset_ops.Dataset.from_tensor_slices(vars_nested)

  def _broadcast_to(self, tensor, destinations):
    if destinations is None:
      return tensor
    else:
      raise NotImplementedError("TODO")

  def _call_for_each_replica(self, fn, args, kwargs):
    with ReplicaContext(
        self._container_strategy(),
        replica_id_in_sync_group=constant_op.constant(0, dtypes.int32)):
      return fn(*args, **kwargs)

  def _reduce_to(self, reduce_op, value, destinations):
    # TODO(josh11b): Use destinations?
    del reduce_op, destinations
    return value

  def _update(self, var, fn, args, kwargs, group):
    # The implementations of _update() and _update_non_slot() are identical
    # except _update() passes `var` as the first argument to `fn()`.
    return self._update_non_slot(var, fn, (var,) + tuple(args), kwargs, group)

  def _update_non_slot(self, colocate_with, fn, args, kwargs, should_group):
    # TODO(josh11b): Figure out what we should be passing to UpdateContext()
    # once that value is used for something.
    with UpdateContext(colocate_with):
      result = fn(*args, **kwargs)
      if should_group:
        return result
      else:
        return nest.map_structure(self._local_results, result)

  def read_var(self, replica_local_var):
    return array_ops.identity(replica_local_var)

  def _local_results(self, distributed_value):
    return (distributed_value,)

  def value_container(self, value):
    return value

  @property
  def _num_replicas_in_sync(self):
    return 1

  @property
  def worker_devices(self):
    raise RuntimeError("worker_devices() method unsupported by default "
                       "tf.distribute.Strategy.")

  @property
  def parameter_devices(self):
    raise RuntimeError("parameter_devices() method unsupported by default "
                       "tf.distribute.Strategy.")

  def non_slot_devices(self, var_list):
    return min(var_list, key=lambda x: x.name)

  # TODO(priyag): This should inherit from `InputIterator`, once dependency
  # issues have been resolved.
  class DefaultInputIterator(object):
    """Default implementation of `InputIterator` for default strategy."""

    def __init__(self, dataset):
      self._dataset = dataset
      if eager_context.executing_eagerly():
        self._iterator = dataset.make_one_shot_iterator()
      else:
        self._iterator = dataset.make_initializable_iterator()

    def get_next(self):
      return self._iterator.get_next()

    def initialize(self):
      if eager_context.executing_eagerly():
        self._iterator = self._dataset.make_one_shot_iterator()
        return []
      else:
        return [self._iterator.initializer]

  # TODO(priyag): Delete this once all strategies use global batch size.
  @property
  def _global_batch_size(self):
    """Global and per-replica batching are equivalent for this strategy."""
    return True


# ------------------------------------------------------------------------------
# We haven't yet implemented deserialization for DistributedVariables.
# So here we catch any attempts to deserialize variables
# when using distribution strategies.
# pylint: disable=protected-access
_original_from_proto = resource_variable_ops._from_proto_fn


def _from_proto_fn(v, import_scope=None):
  if distribution_strategy_context.has_strategy():
    raise NotImplementedError(
        "Deserialization of variables is not yet supported when using a "
        "tf.distribute.Strategy.")
  else:
    return _original_from_proto(v, import_scope=import_scope)

resource_variable_ops._from_proto_fn = _from_proto_fn
# pylint: enable=protected-access


#-------------------------------------------------------------------------------
# Shorthand for some methods from distribution_strategy_context.
_push_per_thread_mode = distribution_strategy_context._push_per_thread_mode  # pylint: disable=protected-access
_get_per_thread_mode = distribution_strategy_context._get_per_thread_mode  # pylint: disable=protected-access
_pop_per_thread_mode = distribution_strategy_context._pop_per_thread_mode  # pylint: disable=protected-access
_get_default_replica_mode = (
    distribution_strategy_context._get_default_replica_mode)  # pylint: disable=protected-access
