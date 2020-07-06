'''
本次任务的内容：实现一个xla HLO pass, 将两个浮点数的乘积自动四舍五入为整数
下面的脚本程序在正常的tensorflow环境下会报断言错误，如果pass实现正确，打开XLA后，该脚本应该能运行通过
'''
import warnings
warnings.filterwarnings('ignore')
import tensorflow as tf
import os
# 下面的标志只对JIT有用
# os.environ["TF_XLA_FLAGS"] = "--tf_xla_auto_jit=2"
# os.environ["XLA_FLAGS"] = "--xla_dump_to=/tmp/xla_generated"

def calc(x, y):
    return tf.multiply(x, y)

in1 = tf.placeholder(tf.float32, shape=(), name="in1")
in2 = tf.placeholder(tf.float32, shape=(), name="in2")
out = tf.xla.experimental.compile(calc, [in1, in2])

test_cases = [
    (1, 4), 
    (1.5, 1.5), 
    (10.7 * 3.6)
]
sess = tf.Session()
for val1, val2 in test_cases:
    result1 = sess.run(out, feed_dict={"in1:0":val1, "in2:0":val2})[0]
    result2 = round(val1 * val2)
    assert result1 == result2, "failed case: %s vs. %s" % (result1, result2)
print("PASS!!!")