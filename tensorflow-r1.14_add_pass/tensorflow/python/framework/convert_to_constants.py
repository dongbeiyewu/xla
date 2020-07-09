# Copyright 2019 The TensorFlow Authors. All Rights Reserved.
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
"""Helpers to convert variables to constants in TensorFlow 2.0."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from tensorflow.core.framework import attr_value_pb2
from tensorflow.core.framework import graph_pb2
from tensorflow.core.framework import variable_pb2
from tensorflow.core.protobuf import config_pb2
from tensorflow.core.protobuf import meta_graph_pb2
from tensorflow.python.eager import wrap_function
from tensorflow.python.framework import tensor_util
from tensorflow.python.grappler import tf_optimizer
from tensorflow.python.ops import array_ops
from tensorflow.python.platform import tf_logging as logging
from tensorflow.python.training.saver import export_meta_graph


def _run_inline_graph_optimization(func):
  """Apply function inline optimization to the graph.

  Returns the GraphDef after Grappler's function inlining optimization is
  applied. This optimization does not work on models with control flow.

  Args:
    func: ConcreteFunction.

  Returns:
    GraphDef
  """
  meta_graph = export_meta_graph(
      graph_def=func.graph.as_graph_def(), graph=func.graph)

  # Clear the initializer_name for the variables collections, since they are not
  # needed after saved to saved_model.
  for name in [
      "variables", "model_variables", "trainable_variables", "local_variables"
  ]:
    raw_list = []
    for raw in meta_graph.collection_def["variables"].bytes_list.value:
      variable = variable_pb2.VariableDef()
      variable.ParseFromString(raw)
      variable.ClearField("initializer_name")
      raw_list.append(variable.SerializeToString())
    meta_graph.collection_def[name].bytes_list.value[:] = raw_list

  # Add a collection 'train_op' so that Grappler knows the outputs.
  fetch_collection = meta_graph_pb2.CollectionDef()
  for array in func.inputs + func.outputs:
    fetch_collection.node_list.value.append(array.name)
  meta_graph.collection_def["train_op"].CopyFrom(fetch_collection)

  # Initialize RewriterConfig with everything disabled except function inlining.
  config = config_pb2.ConfigProto()
  rewrite_options = config.graph_options.rewrite_options
  rewrite_options.min_graph_nodes = -1  # do not skip small graphs
  rewrite_options.optimizers.append("function")
  return tf_optimizer.OptimizeGraph(config, meta_graph)


def _get_tensors_from_graph(graph, tensors):
  """Gets the Tensors in `graph` with the name of the tensors in `tensors`.

  Args:
    graph: TensorFlow Graph.
    tensors: List of Tensors.

  Returns:
    List of Tensors.
  """
  new_tensors = []
  for orig_tensor in tensors:
    new_tensor = graph.get_tensor_by_name(orig_tensor.name)
    if new_tensor.shape.rank is None:
      new_tensor.set_shape(orig_tensor.shape)
    new_tensors.append(new_tensor)
  return new_tensors


def convert_variables_to_constants_v2(func):
  """Replaces all the variables in a graph with constants of the same values.

  TensorFlow 2.0 function for converting all Variable ops into Const ops holding
  the same values. This makes it possible to describe the network fully with a
  single GraphDef file, and allows the removal of a lot of ops related to
  loading and saving the variables. This function runs Grappler's function
  inlining optimization in order to return a single subgraph.

  The current implementation only works for graphs that do not contain any
  control flow or embedding related ops.

  Args:
    func: ConcreteFunction.

  Returns:
    ConcreteFunction containing a simplified version of the original.
  """
  # TODO(nupurgarg): Replace ResourceGather with Gather.
  # TODO(nupurgarg): Change attr for Variables in control flow and functions.
  graph_def = _run_inline_graph_optimization(func)

  # Identify the ReadVariableOps.
  get_name = lambda name: name.split(":")[0]
  map_name_to_node = {get_name(node.name): node for node in graph_def.node}

  # TODO(b/125838789): Use `func.graph.captures`.
  # Get mapping from input name to variable value.
  tensor_data = {}
  map_name_to_handle = {}
  input_tensors = func.inputs[-len(func.captured_inputs):]
  for var in func.graph.variables:
    index = func.captured_inputs.index(var.handle)
    tensor_name = get_name(input_tensors[index].name)
    tensor_data[tensor_name] = var.numpy()
    map_name_to_handle[tensor_name] = var.handle

  # Get mapping from input name to value for non-variable placeholders.
  map_name_to_value = {}
  for name_tensor, value_tensor in zip(input_tensors, func.captured_inputs):
    tensor_name = get_name(name_tensor.name)
    if tensor_name not in map_name_to_handle:
      map_name_to_value[tensor_name] = value_tensor

  resource_identities = {}
  placeholders = {}
  converted_input_indices = set()
  reference_variables = []
  for node in graph_def.node:
    if node.name in map_name_to_value:
      # Get the dtype and data for the Placeholders whose values are stored as
      # Tensors. This is the case for values that were originally Const ops.
      tensor = map_name_to_value[node.name]
      placeholders[node.name] = {
          "dtype": node.attr["dtype"],
          "data": tensor.numpy(),
      }
      converted_input_indices.add(
          func.captured_inputs.index(map_name_to_value[node.name]))
    # Collect the reference variables that cannot be lifted.
    if node.op == "VariableV2":
      reference_variables.append(node)
    if node.op == "ReadVariableOp":
      # Get name of Placeholder op associated with ReadVariableOp. There can be
      # an Identity in between the ReadVariableOp and Placeholder. Store the
      # Identity ops with the associated dtypes.
      input_name = get_name(node.input[0])
      while map_name_to_node[input_name].op == "Identity":
        resource_identities[input_name] = node.attr["dtype"]
        input_name = get_name(map_name_to_node[input_name].input[0])
      if map_name_to_node[input_name].op != "Placeholder":
        raise ValueError("Cannot find the Placeholder op that is an input "
                         "to the ReadVariableOp.")
      # Build a map of Placeholder ops that are inputs to ReadVariableOps to the
      # variable's dtype and data.
      placeholders[input_name] = {
          "dtype": node.attr["dtype"],
          "data": tensor_data[input_name],
      }
      converted_input_indices.add(
          func.captured_inputs.index(map_name_to_handle[input_name]))

  # Reconstruct the graph with constants in place of variables.
  output_graph_def = graph_pb2.GraphDef()
  how_many_converted = 0

  # Add identity node after the reference variable and get the tensor values
  # for them.
  if reference_variables:
    reference_variable_tensors = []
    with func.graph.as_default():
      for node in reference_variables:
        identity_node = array_ops.identity(
            func.graph.as_graph_element(node.name + ":0"))
        reference_variable_tensors.append(identity_node.name)

    reference_variable_values = func.prune([], reference_variable_tensors)()

    # Add values of reference variables as constant nodes.
    for node, value in zip(reference_variables, reference_variable_values):
      output_node = output_graph_def.node.add()
      dtype = attr_value_pb2.AttrValue()
      dtype.type = value.dtype.as_datatype_enum

      output_node.op = "Const"
      output_node.name = node.name
      output_node.attr["dtype"].CopyFrom(dtype)
      output_node.attr["value"].tensor.CopyFrom(
          tensor_util.make_tensor_proto(value))
      how_many_converted += 1

  for input_node in graph_def.node:
    # Skip VariableV2 node, since their values are added by the identity nodes.
    if input_node.op == "VariableV2":
      continue
    output_node = output_graph_def.node.add()
    # Convert Placeholder ops to Const ops.
    if input_node.name in placeholders:
      dtype = placeholders[input_node.name]["dtype"]
      data = placeholders[input_node.name]["data"]

      output_node.op = "Const"
      output_node.name = input_node.name
      output_node.attr["dtype"].CopyFrom(dtype)
      output_node.attr["value"].tensor.CopyFrom(
          tensor_util.make_tensor_proto(
              data, dtype=dtype.type, shape=data.shape))
      how_many_converted += 1
    # Change the dtype for Identity ops that are inputs to ReadVariableOps.
    elif input_node.name in resource_identities:
      output_node.CopyFrom(input_node)
      output_node.attr["T"].CopyFrom(resource_identities[input_node.name])
    # Convert ReadVariableOps into Identity ops.
    elif input_node.op == "ReadVariableOp":
      output_node.op = "Identity"
      output_node.name = input_node.name
      output_node.input.extend([input_node.input[0]])
      output_node.attr["T"].CopyFrom(input_node.attr["dtype"])
      if "_class" in input_node.attr:
        output_node.attr["_class"].CopyFrom(input_node.attr["_class"])
    else:
      output_node.CopyFrom(input_node)

  logging.info("Converted %d variables to const ops.", how_many_converted)

  # Create a ConcreteFunction from the new GraphDef.
  converted_inputs = set(
      [input_tensors[index] for index in converted_input_indices])
  not_converted_inputs = set(func.inputs).difference(converted_inputs)
  not_converted_inputs_map = {
      tensor.name: tensor for tensor in not_converted_inputs
  }

  new_input_names = [tensor.name for tensor in not_converted_inputs]
  new_output_names = [tensor.name for tensor in func.outputs]
  new_func = wrap_function.function_from_graph_def(output_graph_def,
                                                   new_input_names,
                                                   new_output_names)

  # Manually propagate shape for input tensors where the shape is not correctly
  # propagated. Scalars shapes are lost when wrapping the function.
  for input_tensor in new_func.inputs:
    input_tensor.set_shape(not_converted_inputs_map[input_tensor.name].shape)
  return new_func
