## MNIST数据集
使用 `input_data` 模块中的 `read_data_sets() `函数加载 mnist 数据集：

>`from tensorflow.examples.tutorials.mnist import input_data `</br>
`mnist = input_data.read_data_sets('./data/',one_hot=True)`

在 `read_data_sets()` 函数中有两个参数，第一个参数表示数据集存放路径，第二个参数表示数据集的存取形式。
当第二个参数为` Ture `时，表示以独热码形式存取数据集。`read_data_sets() `函数运行时，会检查指定路径内是否已经有数据集，
若指定路径中没有数据集，则自动下载，并将 `mnist `数据集分为训练集 `train`、验证集 `validation `和测试集 test 存放。

`mnist = input_data.read_data_sets('./data/',one_hot=True)`直接下载会报错,由于版本迭代read_data_sets这个函数以后就不存在了（比如升级tf版本之后）现在还是能用的

>` WARNING:tensorflow:From /home/sun/tf-demo/tensorflow-dev/lib/python3.5/site-packages/tensorflow/contrib/learn/python/learn/datasets/base.py:252: _internal_retry.<locals>.wrap.<locals>.wrapped_fn (from tensorflow.contrib.learn.python.learn.datasets.base) is deprecated and will be removed in a future version.
Instructions for updating:
Please use urllib or similar directly.`

为了解决这个问题，可以先将图集下载好，[点击下载mnist训练集](http://yann.lecun.com/exdb/mnist/)

或使用命令下载

>`wget -b -i url.txt`

url.txt内容为

>`http://yann.lecun.com/exdb/mnist/train-images-idx3-ubyte.gz`</br>
`http://yann.lecun.com/exdb/mnist/train-labels-idx1-ubyte.gz`</br>
`http://yann.lecun.com/exdb/mnist/t10k-images-idx3-ubyte.gz`</br>
`http://yann.lecun.com/exdb/mnist/t10k-labels-idx1-ubyte.gz`

下载后执行命令
>`mnist = input_data.read_data_sets('./data/',one_hot=True)`

在终端显示如下内容：

>`Extracting ./data/train-images-idx3-ubyte.gz `训练集图像（9912422字节）</br>
`Extracting ./data/train-labels-idx1-ubyte.gz` 训练集标签（
28881 字节）</br>
`Extracting ./data/tl0k-images-idx3-ubyte.gz ` 测试集图像（1648877字节）</br>
`Extracting ./data/ tl0k-labels-idx1-ubyte.gz` 测试集标签（4542字节）

查看训练集中第 0 张图片的标签

>`mnist.train.labels[0]`

使用· train.images ·函数返回 mnist 数据集图片像素值

【例如】：
在 mnist 数据集中，若想要查看训练集中第 0 张图片像素值，则使用如下函数 
>`mnist.train.images[0]`


##  手写数字识别准确率输出

实现手写体 mnist 数据集的识别任务，共分为三个模块文件，分别是描述网络结构的前向传播过程文件`mnist_forward.py`、描述网络参数优化方法的反向传播过程文件`mnist_backward.py`、验证模型准确率的测试过程文件`mnist_test.py`。


### **前向传播过程文件**
前向传播过程文件`mnist_forward.py`
在前向传播过程中，需要定义网络模型输入层个数、隐藏层节点数、输出层个数，定义网络参数 w、偏置 b，定义由输入到输出的神经网络架构。

实现手写体 mnist 数据集的识别任务前向传播过程如下：

```python
import tensorflow as tf

INPUT_NODE = 784
OUTPUT_NODE = 10
LAYER1_NODE = 500

def get_weight(shape, regularizer):
    w = tf.Variable(tf.truncated_normal(shape,stddev=0.1))
    if regularizer != None: 
        tf.add_to_collection(
            'losses', 
            tf.contrib.layers.l2_regularizer(regularizer)(w)
        )
    return w


def get_bias(shape):  
    b = tf.Variable(tf.zeros(shape))  
    return b
    
def forward(x, regularizer):
    w1 = get_weight([INPUT_NODE, LAYER1_NODE], regularizer)
    b1 = get_bias([LAYER1_NODE])
    y1 = tf.nn.relu(tf.matmul(x, w1) + b1)

    w2 = get_weight([LAYER1_NODE, OUTPUT_NODE], regularizer)
    b2 = get_bias([OUTPUT_NODE])
    y = tf.matmul(y1, w2) + b2
    return y
```

### **反向传播过程文件**
反向传播过程实现利用训练数据集对神经网络模型训练，通过降低损失函数值，实现网络模型参数的优化，从而得到准确率高且泛化能力强的神经网络模型。 实现手写体 mnist 数据集的识别任务反向传播过程如下：

```python
import tensorflow as tf
from tensorflow.examples.tutorials.mnist import input_data
import mnist_forward
import os

BATCH_SIZE = 200
LEARNING_RATE_BASE = 0.1
LEARNING_RATE_DECAY = 0.99
REGULARIZER = 0.0001
STEPS = 50000
MOVING_AVERAGE_DECAY = 0.99
MODEL_SAVE_PATH="./model/"
MODEL_NAME="mnist_model"


def backward(mnist):

    x = tf.placeholder(tf.float32, [None, mnist_forward.INPUT_NODE])
    y_ = tf.placeholder(tf.float32, [None, mnist_forward.OUTPUT_NODE])
    y = mnist_forward.forward(x, REGULARIZER)
    global_step = tf.Variable(0, trainable=False)

    ce = tf.nn.sparse_softmax_cross_entropy_with_logits(logits=y, labels=tf.argmax(y_, 1))
    cem = tf.reduce_mean(ce)
    loss = cem + tf.add_n(tf.get_collection('losses'))

    learning_rate = tf.train.exponential_decay(
        LEARNING_RATE_BASE,
        global_step,
        mnist.train.num_examples / BATCH_SIZE, 
        LEARNING_RATE_DECAY,
        staircase=True)

    train_step = tf.train.GradientDescentOptimizer(learning_rate).minimize(loss, global_step=global_step)

    ema = tf.train.ExponentialMovingAverage(MOVING_AVERAGE_DECAY, global_step)
    ema_op = ema.apply(tf.trainable_variables())
    with tf.control_dependencies([train_step, ema_op]):
        train_op = tf.no_op(name='train')

    saver = tf.train.Saver()

    with tf.Session() as sess:
        init_op = tf.global_variables_initializer()
        sess.run(init_op)

        for i in range(STEPS):
            xs, ys = mnist.train.next_batch(BATCH_SIZE)
            _, loss_value, step = sess.run([train_op, loss, global_step], feed_dict={x: xs, y_: ys})
            if i % 1000 == 0:
                print("After %d training step(s), loss on training batch is %g." % (step, loss_value))
                saver.save(sess, os.path.join(MODEL_SAVE_PATH, MODEL_NAME), global_step=global_step)


def main():
    mnist = input_data.read_data_sets("./data/", one_hot=True)
    backward(mnist)

if __name__ == '__main__':
    main()
```

## **测试过程文件**

```python
# coding:utf-8
import time
import tensorflow as tf
from tensorflow.examples.tutorials.mnist import input_data
import mnist_forward
import mnist_backward

TEST_INTERVAL_SECS = 5

def test(mnist):
    with tf.Graph().as_default() as g:
        x = tf.placeholder(tf.float32, [None, mnist_forward.INPUT_NODE])
        y_ = tf.placeholder(tf.float32, [None, mnist_forward.OUTPUT_NODE])
        y = mnist_forward.forward(x, None)

        ema = tf.train.ExponentialMovingAverage(mnist_backward.MOVING_AVERAGE_DECAY)
        ema_restore = ema.variables_to_restore()
        saver = tf.train.Saver(ema_restore)
        
        correct_prediction = tf.equal(tf.argmax(y, 1), tf.argmax(y_, 1))
        accuracy = tf.reduce_mean(tf.cast(correct_prediction, tf.float32))

        while True:
            with tf.Session() as sess:
                ckpt = tf.train.get_checkpoint_state(mnist_backward.MODEL_SAVE_PATH)
                if ckpt and ckpt.model_checkpoint_path:
                    saver.restore(sess, ckpt.model_checkpoint_path)
                    global_step = ckpt.model_checkpoint_path.split('/')[-1].split('-')[-1]
                    accuracy_score = sess.run(accuracy, feed_dict={x: mnist.test.images, y_: mnist.test.labels})
                    print("After %s training step(s), test accuracy = %g" % (global_step, accuracy_score))
                else:
                    print('No checkpoint file found')
                    return
            time.sleep(TEST_INTERVAL_SECS)

def main():
    mnist = input_data.read_data_sets("./data/", one_hot=True)
    test(mnist)

if __name__ == '__main__':
    main()
```

运行mnist_backward.py

![1](https://github.com/erguixieshen/XLA/raw/master/week1/picture/10.png)

运行mnist_test.py

![1](https://github.com/erguixieshen/XLA/raw/master/week1/picture/11.png)

从终端显示的运行结果可以看出，随着训练轮数的增加，网络模型的损失函数值在不断降低，并且在测试集上的准确率在不断提升，有较好的泛化能力。
