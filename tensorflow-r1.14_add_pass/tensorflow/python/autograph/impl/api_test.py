# Copyright 2017 The TensorFlow Authors. All Rights Reserved.
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
"""Tests for api module."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import functools
import gc
import imp
import os
import re
import textwrap
import types

import numpy as np

from tensorflow.python.autograph import utils
from tensorflow.python.autograph.core import converter
from tensorflow.python.autograph.impl import api
from tensorflow.python.autograph.pyct import inspect_utils
from tensorflow.python.autograph.pyct import parser
from tensorflow.python.autograph.utils import py_func
from tensorflow.python.eager import function
from tensorflow.python.framework import constant_op
from tensorflow.python.framework import test_util
from tensorflow.python.keras.engine import sequential
from tensorflow.python.keras.layers import core
from tensorflow.python.ops import gen_math_ops
from tensorflow.python.ops import variables
from tensorflow.python.platform import test
from tensorflow.python.util import tf_inspect

tf = utils.fake_tf()


testing_global_numeric = 2


class TestResource(object):

  def __init__(self):
    self.x = 3


class ApiTest(test.TestCase):

  @test_util.run_deprecated_v1
  def test_decorator_recursive(self):

    class TestClass(object):

      def called_member(self, a):
        if a < 0:
          a = -a
        return a

      @api.convert(recursive=True)
      def test_method(self, x, s, a):
        while tf.reduce_sum(x) > s:
          x //= self.called_member(a)
        return x

    tc = TestClass()
    with self.cached_session() as sess:
      x = tc.test_method(
          constant_op.constant([2, 4]), constant_op.constant(1),
          constant_op.constant(-2))
      self.assertListEqual([0, 1], self.evaluate(x).tolist())

  @test_util.run_deprecated_v1
  def test_decorator_not_recursive(self):

    class TestClass(object):

      def called_member(self, a):
        return tf.negative(a)

      @api.convert(recursive=False)
      def test_method(self, x, s, a):
        while tf.reduce_sum(x) > s:
          x //= self.called_member(a)
        return x

    tc = TestClass()
    with self.cached_session() as sess:
      x = tc.test_method(
          constant_op.constant([2, 4]), constant_op.constant(1),
          constant_op.constant(-2))
      self.assertListEqual([0, 1], self.evaluate(x).tolist())

  @test_util.run_deprecated_v1
  def test_convert_then_do_not_convert_graph(self):

    class TestClass(object):

      @api.do_not_convert(api.RunMode.GRAPH)
      def called_member(self, a):
        return tf.negative(a)

      @api.convert(recursive=True)
      def test_method(self, x, s, a):
        while tf.reduce_sum(x) > s:
          x //= self.called_member(a)
        return x

    tc = TestClass()
    x = tc.test_method(
        constant_op.constant((2, 4)), constant_op.constant(1),
        constant_op.constant(-2))
    self.assertAllEqual((0, 1), self.evaluate(x))

  @test_util.run_deprecated_v1
  def test_convert_then_do_not_convert_py_func(self):

    class TestClass(object):

      @api.do_not_convert(
          api.RunMode.PY_FUNC, return_dtypes=py_func.MatchDType(1))
      def called_member(self, a):
        return np.negative(a)

      @api.convert(recursive=True)
      def test_method(self, x, s, a):
        while tf.reduce_sum(x) > s:
          y = self.called_member(a)
          # set_shape works around while_loop's limitations.
          # TODO(mdan): Allow specifying shapes (or ShapeLike) instead.
          y.set_shape(a.shape)
          x //= y
        return x

    tc = TestClass()
    x = tc.test_method(
        constant_op.constant((2, 4)), constant_op.constant(1),
        constant_op.constant(-2))
    self.assertAllEqual((0, 1), self.evaluate(x))

  @test_util.run_deprecated_v1
  def test_decorator_calls_decorated(self):

    class TestClass(object):

      @api.convert()
      def called_member(self, a):
        if a < 0:
          a = -a
        return a

      @api.convert(recursive=True)
      def test_method(self, x, s, a):
        while tf.reduce_sum(x) > s:
          x //= self.called_member(a)
        return x

    tc = TestClass()
    with self.cached_session() as sess:
      x = tc.test_method(
          constant_op.constant([2, 4]), constant_op.constant(1),
          constant_op.constant(-2))
      self.assertListEqual([0, 1], self.evaluate(x).tolist())

  def test_decorator_preserves_argspec(self):

    class TestClass(object):

      def called_member(self, a):
        if a < 0:
          a = -a
        return a

      called_member_converted = api.convert()(called_member)

    tc = TestClass()
    self.assertListEqual(
        list(tf_inspect.getfullargspec(tc.called_member)),
        list(tf_inspect.getfullargspec(tc.called_member_converted)))

  @test_util.run_deprecated_v1
  def test_convert_call_site_decorator(self):

    class TestClass(object):

      def called_member(self, a):
        if a < 0:
          a = -a
        return a

      @api.convert(recursive=True)
      def test_method(self, x, s, a):
        while tf.reduce_sum(x) > s:
          x //= api.converted_call(self.called_member, None,
                                   converter.ConversionOptions(recursive=True),
                                   (a,), {})
        return x

    tc = TestClass()
    x = tc.test_method(
        constant_op.constant([2, 4]), constant_op.constant(1),
        constant_op.constant(-2))
    self.assertListEqual([0, 1], self.evaluate(x).tolist())

  def test_converted_call_builtin(self):
    x = api.converted_call(range, None,
                           converter.ConversionOptions(recursive=True), (3,),
                           {})
    self.assertEqual((0, 1, 2), tuple(x))

    x = api.converted_call('compile', re,
                           converter.ConversionOptions(recursive=True),
                           ('mnas_v4_a.*\\/.*(weights|kernel):0$',), {})
    self.assertIsNotNone(x.match('mnas_v4_a/weights:0'))

  def test_converted_call_function(self):

    def test_fn(x):
      if x < 0:
        return -x
      return x

    x = api.converted_call(test_fn, None,
                           converter.ConversionOptions(recursive=True),
                           (constant_op.constant(-1),), {})
    self.assertEqual(1, self.evaluate(x))

  @test_util.run_v1_only('b/120545219')
  def test_converted_call_functools_partial(self):

    def test_fn(x, y, z):
      if x < 0:
        return -x, -y, -z
      return x, y, z

    x = api.converted_call(
        functools.partial(test_fn, constant_op.constant(-1), z=-3), None,
        converter.ConversionOptions(recursive=True),
        (constant_op.constant(-2),), {})
    self.assertEqual((1, 2, 3), self.evaluate(x))

    x = api.converted_call(
        functools.partial(
            functools.partial(test_fn, constant_op.constant(-1)), z=-3), None,
        converter.ConversionOptions(recursive=True),
        (constant_op.constant(-2),), {})
    self.assertEqual((1, 2, 3), self.evaluate(x))

  def test_converted_call_method_explicit_owner(self):
    # TODO(mdan): Implement.
    pass

  def test_converted_call_method_explicit_super_owner(self):
    # TODO(mdan): Implement.
    pass

  def test_converted_call_method(self):

    class TestClass(object):

      def __init__(self, x):
        self.x = x

      def test_method(self):
        if self.x < 0:
          return -self.x
        return self.x

    tc = TestClass(constant_op.constant(-1))
    x = api.converted_call(tc.test_method, None,
                           converter.ConversionOptions(recursive=True), (), {})
    self.assertEqual(1, self.evaluate(x))

  def test_converted_call_synthetic_method(self):

    class TestClass(object):

      def __init__(self, x):
        self.x = x

    def test_function(self):
      if self.x < 0:
        return -self.x
      return self.x

    tc = TestClass(constant_op.constant(-1))
    test_method = types.MethodType(test_function, tc)

    x = api.converted_call(test_method, None,
                           converter.ConversionOptions(recursive=True), (), {})
    self.assertEqual(1, self.evaluate(x))

  def test_converted_call_method_wrapper(self):

    class TestClass(object):

      def foo(self):
        pass

    tc = TestClass()

    # `method.__get__()` returns a so-called method-wrapper.
    wrapper = api.converted_call('__get__', tc.foo,
                                 converter.ConversionOptions(recursive=True),
                                 (tc,), {})
    self.assertEqual(wrapper, tc.foo)

  def test_converted_call_method_as_object_attribute(self):

    class AnotherClass(object):

      def __init__(self):
        self.another_class_attr = constant_op.constant(1)

      def method(self):
        if self.another_class_attr > 0:
          return self.another_class_attr + 1
        return self.another_class_attr + 10

    class TestClass(object):

      def __init__(self, another_obj_method):
        self.another_obj_method = another_obj_method

    obj = AnotherClass()
    tc = TestClass(obj.method)

    x = api.converted_call('another_obj_method', tc,
                           converter.ConversionOptions(recursive=True), (), {})
    self.assertEqual(self.evaluate(x), 2)

  def test_converted_call_method_converts_recursively(self):

    class TestClass(object):

      def __init__(self, x):
        self.x = x

      def other_method(self):
        if self.x < 0:
          return -self.x
        return self.x

      def test_method(self):
        return self.other_method()

    tc = TestClass(constant_op.constant(-1))
    x = api.converted_call(tc.test_method, None,
                           converter.ConversionOptions(recursive=True), (), {})
    self.assertEqual(1, self.evaluate(x))

  def test_converted_call_method_by_class(self):

    class TestClass(object):

      def __init__(self, x):
        self.x = x

      def test_method(self):
        if self.x < 0:
          return -self.x
        return self.x

    tc = TestClass(constant_op.constant(-1))
    x = api.converted_call(TestClass.test_method, None,
                           converter.ConversionOptions(recursive=True), (tc,),
                           {})
    self.assertEqual(1, self.evaluate(x))

  def test_converted_call_callable_object(self):

    class TestClass(object):

      def __init__(self, x):
        self.x = x

      def __call__(self):
        if self.x < 0:
          return -self.x
        return self.x

    tc = TestClass(constant_op.constant(-1))
    x = api.converted_call(tc, None,
                           converter.ConversionOptions(recursive=True), (), {})
    self.assertEqual(1, self.evaluate(x))

  @test_util.run_deprecated_v1
  def test_converted_call_constructor(self):

    class TestClass(object):

      def __init__(self, x):
        self.x = x

      def test_method(self):
        if self.x < 0:
          return -self.x
        return self.x

    tc = api.converted_call(TestClass, None,
                            converter.ConversionOptions(recursive=True),
                            (constant_op.constant(-1),), {})
    # tc is still a TestClass - constructors are whitelisted.
    # TODO(b/124016764): Support this use case.
    # The error below is specific to the `if` statement not being converted.
    with self.assertRaisesRegex(
        TypeError, 'Using a `tf.Tensor` as a Python `bool`'):
      tc.test_method()

  def test_converted_call_already_converted(self):

    def f(x):
      return x == 0

    x = api.converted_call(f, None, converter.ConversionOptions(recursive=True),
                           (constant_op.constant(0),), {})
    self.assertTrue(self.evaluate(x))

    converted_f = api.to_graph(
        f, experimental_optional_features=converter.Feature.ALL)
    x = api.converted_call(converted_f, None,
                           converter.ConversionOptions(recursive=True),
                           (constant_op.constant(0),), {})
    self.assertTrue(self.evaluate(x))

  def test_converted_call_then_already_converted_dynamic(self):

    @api.convert()
    def g(x):
      if x > 0:
        return x
      else:
        return -x

    def f(g, x):
      return g(x)

    x = api.converted_call(f, None, converter.ConversionOptions(recursive=True),
                           (g, constant_op.constant(1)), {})
    self.assertEqual(self.evaluate(x), 1)

  @test_util.run_deprecated_v1
  def test_converted_call_no_user_code(self):

    def f(x):
      return len(x)

    opts = converter.ConversionOptions(internal_convert_user_code=False)

    # f should not be converted, causing len to error out.
    with self.assertRaisesRegexp(Exception,
                                 'object of type \'Tensor\' has no len()'):
      api.converted_call(f, None, opts, (constant_op.constant([0]),), {})

    # len on the other hand should work fine.
    x = api.converted_call(len, None, opts, (constant_op.constant([0]),), {})
    # The constant has static shape so the result is a primitive not a Tensor.
    self.assertEqual(x, 1)

  def test_converted_call_no_kwargs_allowed(self):

    def f(*args):
      # Note: np.broadcast rejects any **kwargs, even *{}
      return np.broadcast(args[:1])

    opts = converter.ConversionOptions(internal_convert_user_code=False)

    self.assertIsNotNone(api.converted_call(f, None, opts, (1, 2, 3, 4), None))

  def test_converted_call_whitelisted_method(self):

    opts = converter.ConversionOptions(recursive=True)

    model = sequential.Sequential([
        core.Dense(2)
    ])

    x = api.converted_call(model.call, None, opts,
                           (constant_op.constant([[0.0]]),), {'training': True})

    self.evaluate(variables.global_variables_initializer())
    self.assertAllEqual([[0.0, 0.0]], self.evaluate(x))

  def test_converted_call_whitelisted_method_via_owner(self):

    opts = converter.ConversionOptions(recursive=True)

    model = sequential.Sequential([
        core.Dense(2)
    ])

    x = api.converted_call('call', model, opts,
                           (constant_op.constant([[0.0]]),), {'training': True})

    self.evaluate(variables.global_variables_initializer())
    self.assertAllEqual([[0.0, 0.0]], self.evaluate(x))

  def test_converted_call_numpy(self):

    opts = converter.ConversionOptions(recursive=True)

    x = api.converted_call(np.arange, None, opts, (5,), {})

    self.assertAllEqual(x, list(range(5)))

  def test_converted_call_tf_op_forced(self):

    # TODO(mdan): Add the missing level of support to LOGICAL_EXPRESSIONS.
    opts = converter.ConversionOptions(
        force_conversion=True, optional_features=None)

    x = api.converted_call(gen_math_ops.add, None, opts, (1, 1), {})

    self.assertAllEqual(self.evaluate(x), 2)

  def test_converted_call_exec_generated_code(self):

    temp_mod = imp.new_module('test_module')
    dynamic_code = '''
      def foo(x):
        return x + 1
    '''
    exec(textwrap.dedent(dynamic_code), temp_mod.__dict__)  # pylint:disable=exec-used
    opts = converter.ConversionOptions(optional_features=None)

    x = api.converted_call(temp_mod.foo, None, opts, (1,), {})

    self.assertAllEqual(x, 2)

  def test_converted_call_namedtuple(self):

    opts = converter.ConversionOptions(recursive=True)

    x = api.converted_call(collections.namedtuple, None, opts,
                           ('TestNamedtuple', ('a', 'b')), {})

    self.assertTrue(inspect_utils.isnamedtuple(x))

  def test_converted_call_namedtuple_via_collections(self):

    opts = converter.ConversionOptions(recursive=True)

    x = api.converted_call('namedtuple', collections, opts, ('TestNamedtuple',
                                                             ('a', 'b')), {})

    self.assertTrue(inspect_utils.isnamedtuple(x))

  def test_converted_call_lambda(self):

    opts = converter.ConversionOptions(recursive=True)

    l = lambda x: x == 0

    x = api.converted_call(l, None, opts, (constant_op.constant(0),), {})

    self.evaluate(variables.global_variables_initializer())
    self.assertAllEqual(True, self.evaluate(x))

  def test_converted_call_defun_object_method(self):

    opts = converter.ConversionOptions(recursive=True)

    # pylint:disable=method-hidden
    class TestClass(object):

      def method(self):
        return 1

      def prepare(self):
        self.method = function.defun(self.method)
    # pylint:enable=method-hidden

    tc = TestClass()
    tc.prepare()

    x = api.converted_call(tc.method, None, opts, (), {})

    self.assertAllEqual(1, self.evaluate(x))

  def assertNoMemoryLeaks(self, f):
    object_ids_before = {id(o) for o in gc.get_objects()}
    f()
    gc.collect()
    objects_after = tuple(
        o for o in gc.get_objects() if id(o) not in object_ids_before)
    self.assertEmpty(
        tuple(o for o in objects_after if isinstance(o, TestResource)))

  def test_converted_call_no_leaks_via_closure(self):

    def test_fn():
      res = TestResource()

      def f(y):
        return res.x + y

      opts = converter.ConversionOptions(recursive=True)
      api.converted_call(f, None, opts, (1,), {})

    self.assertNoMemoryLeaks(test_fn)

  def test_converted_call_no_leaks_via_inner_function_closure(self):

    def test_fn():
      res = TestResource()

      def f(y):

        def inner_f():
          return res.x + y

        return inner_f

      opts = converter.ConversionOptions(recursive=True)
      api.converted_call(f, None, opts, (1,), {})()

    self.assertNoMemoryLeaks(test_fn)

  def test_to_graph_basic(self):

    def test_fn(x, s):
      while tf.reduce_sum(x) > s:
        x //= 2
      return x

    compiled_fn = api.to_graph(test_fn)

    with tf.Graph().as_default():
      x = compiled_fn(constant_op.constant((4, 8)), 4)
      self.assertAllEqual(self.evaluate(x), (1, 2))

  @test_util.run_deprecated_v1
  def test_to_graph_with_defaults(self):

    foo = 4

    def test_fn(x, s=foo):
      while tf.reduce_sum(x) > s:
        x //= 2
      return x

    compiled_fn = api.to_graph(test_fn)

    with self.cached_session() as sess:
      x = compiled_fn(constant_op.constant([4, 8]))
      self.assertListEqual([1, 2], self.evaluate(x).tolist())

  def test_to_graph_with_globals(self):

    def test_fn(x):
      global testing_global_numeric
      testing_global_numeric = x + testing_global_numeric
      return testing_global_numeric

    with self.assertRaisesRegex(
        NotImplementedError, 'global keyword is not yet supported'):
      api.to_graph(test_fn)

  def test_to_graph_with_kwargs_clashing_converted_call(self):

    def called_fn(**kwargs):
      return kwargs['f'] + kwargs['owner']

    def test_fn():
      # These arg names intentionally match converted_call's
      return called_fn(f=1, owner=2)

    compiled_fn = api.to_graph(test_fn)

    self.assertEqual(compiled_fn(), 3)

  def test_to_graph_with_kwargs_clashing_unconverted_call(self):

    @api.do_not_convert()
    def called_fn(**kwargs):
      return kwargs['f'] + kwargs['owner']

    def test_fn():
      # These arg names intentionally match _call_unconverted's
      return called_fn(f=1, owner=2)

    compiled_fn = api.to_graph(test_fn)

    self.assertEqual(compiled_fn(), 3)

  def test_to_graph_caching(self):

    def test_fn(x):
      if x > 0:
        return x
      else:
        return -x

    converted_functions = tuple(api.to_graph(test_fn) for _ in (-1, 0, 1))

    # All outputs are from the same module. We can't use __module__ because
    # that's reset when we instantiate the function (see conversion.py).
    # TODO(mdan): Can and should we overwrite __module__ instead?
    module_names = frozenset(f.ag_module for f in converted_functions)
    self.assertEqual(len(module_names), 1)
    self.assertNotIn('__main__', module_names)

    self.assertEqual(len(frozenset(id(f) for f in converted_functions)), 3)

  def test_to_graph_caching_different_options(self):

    def called_fn():
      pass

    def test_fn():
      return called_fn()

    converted_recursive = api.to_graph(test_fn, recursive=True)
    converted_non_recursive = api.to_graph(test_fn, recursive=False)

    self.assertNotEqual(converted_recursive.ag_module,
                        converted_non_recursive.ag_module)
    self.assertIn('internal_convert_user_code=True',
                  tf_inspect.getsource(converted_recursive))
    self.assertNotIn('internal_convert_user_code=False',
                     tf_inspect.getsource(converted_recursive))
    self.assertIn('internal_convert_user_code=False',
                  tf_inspect.getsource(converted_non_recursive))
    self.assertNotIn('internal_convert_user_code=True',
                     tf_inspect.getsource(converted_non_recursive))

  def test_to_graph_preserves_bindings(self):
    y = 3

    def test_fn():
      return y

    converted = api.to_graph(test_fn)

    self.assertEqual(converted(), 3)

    y = 7

    self.assertEqual(converted(), 7)

  def test_to_graph_source_map(self):

    def test_fn(y):
      return y**2

    self.assertTrue(hasattr(api.to_graph(test_fn), 'ag_source_map'))

  def test_to_code_basic(self):

    def test_fn(x, s):
      while tf.reduce_sum(x) > s:
        x /= 2
      return x

    # Just check that the output is parseable Python code.
    self.assertIsNotNone(parser.parse_str(api.to_code(test_fn)))


if __name__ == '__main__':
  os.environ['AUTOGRAPH_STRICT_CONVERSION'] = '1'
  test.main()
