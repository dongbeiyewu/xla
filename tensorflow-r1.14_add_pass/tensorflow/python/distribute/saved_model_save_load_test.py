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
"""Tests for saving and loading using tf's saved_model APIs with DS."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from tensorflow.python.distribute import combinations
from tensorflow.python.distribute import saved_model_test_base as test_base
from tensorflow.python.eager import test
from tensorflow.python.saved_model import saved_model

_DEFAULT_FUNCTION_KEY = 'serving_default'


class SavedModelSaveAndLoadTest(test_base.TestSavedModelBase):

  def setUp(self):
    self._root_dir = 'saved_model_save_load'
    super(SavedModelSaveAndLoadTest, self).setUp()

  def _save_model(self, model, saved_dir):
    saved_model.save(model, saved_dir)

  def _load_and_run_model(self, distribution, saved_dir, predict_dataset,
                          output_name):
    dist_predict_dataset = distribution.experimental_distribute_dataset(
        predict_dataset)
    per_replica_predict_data = next(iter(dist_predict_dataset))
    func = saved_model.load(saved_dir)
    result = distribution.experimental_run_v2(
        func.signatures[_DEFAULT_FUNCTION_KEY], per_replica_predict_data)
    return result[output_name]

  @combinations.generate(test_base.simple_models_with_strategies())
  def test_save_no_strategy_restore_strategy(self, model_and_input,
                                             distribution):
    self.skipTest(('Saving/loading model with tf.distribute.Strategy is not ',
                   'supported.'))
    self.run_test_save_no_strategy_restore_strategy(model_and_input,
                                                    distribution)

  @combinations.generate(test_base.simple_models_with_strategies())
  def test_save_strategy_restore_no_strategy(self, model_and_input,
                                             distribution):
    self.skipTest(('Saving/loading model with tf.distribute.Strategy is not ',
                   'supported.'))
    self.run_test_save_strategy_restore_no_strategy(model_and_input,
                                                    distribution)

  @combinations.generate(test_base.simple_models_with_strategy_pairs())
  def test_save_strategy_restore_strategy(self, model_and_input,
                                          distribution_pair):
    self.skipTest(('Saving/loading model with tf.distribute.Strategy is not ',
                   'supported.'))
    self.run_test_save_strategy_restore_strategy(model_and_input,
                                                 distribution_pair)


if __name__ == '__main__':
  test.main()
