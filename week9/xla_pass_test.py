'''
本次任务的内容：实现一个xla HLO pass, 将两个浮点数的乘积自动四舍五入为整数
下面的脚本程序在正常的tensorflow环境下会报断言错误，如果pass实现正确，打开XLA后，该脚本应该能运行通过
'''
import warnings
warnings.filterwarnings('ignore')
import tensorflow as tf

x = tf.placeholder(tf.float32, name="x")
y = tf.placeholder(tf.float32, name="y") 
out = tf.math.multiply(x, y)

sess = tf.Session()
init = tf.global_variables_initializer()
sess.run(init)

test_cases = [
    (1, 4), 
    (1.5, 1.5), 
    (10.7 * 3.6)
]
for val1, val2 in test_cases:
    assert sess.run(out, feed_dict={"x:0":val1, "y:0":val2}) == round(val1 * val2), "failed case: %s * %s" % (val1, val2)