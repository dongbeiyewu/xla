##  MNIST数据集
mnist 数据集： 包含 7 万张黑底白字手写数字图片，其中 55000 张为训练集， 5000 张为验证集，10000 张为测试集。

每张图片大小为 `28*28 `像素，图片中纯黑色像素值为` 0`，纯白色像素值为` 1`。
数据集的标签是长度为` 10 `的一维数组，数组中每个元素索引号表示对应数字出现的概率。

在将 `mnist` 数据集作为输入喂入神经网络时，需先将数据集中每张图片变为长度 `784` 一维数组，将该数组作为神经网络输入特征喂入神经网络。

【例如】：
一张数字手写体图片变成长度为 `784 `的一维数组` [0. 0. 0. 0. 0.231 0.235 0.459 ... 0.219 0. 0. 0. 0.] `输入神经网络。
该图片对应的标签为` [0. 0. 0. 0. 0. 0. 1. 0. 0. 0]`，标签中索引号为 6 的元素为 1，表示是数字 6 出现的概率为` 100%`，则该图片对应的识别结果是 6。

## 使用 input_data 模块中的 read_data_sets() 函数加载 mnist 数据集：
``` python 
from tensorflow.examples.tutorials.mnist import input_data 
mnist = input_data.read_data_sets('./data/',one_hot=True)
```
在 read_data_sets() 函数中有两个参数，第一个参数表示数据集存放路径，第二个参数表示数据集的存取形式。

当第二个参数为 Ture 时，表示以独热码形式存取数据集。

read_data_sets() 函数运行时，会检查指定路径内是否已经有数据集，
若指定路径中没有数据集，则自动下载，并将 mnist 数据集分为训练集 train、验证集 validation 和测试集 test 存放。在终端显示如下内容：
```
Extracting ./data/train-images-idx3-ubyte.gz 
Extracting ./data/train-labels-idx1-ubyte.gz
Extracting ./data/tl0k-images-idx3-ubyte.gz 
Extracting ./data/ tl0k-labels-idx1-ubyte.gz
```

使用 train.labels 函数返回 mnist 数据集标签

在 mnist 数据集中，若想要查看训练集中第 0 张图片的标签，则使用如下函数` mnist.train.labels[0]`
输出结果：`array([0.,0.,0.,0.,0.,0.,1.,0.,0.,0])`

使用 train.images 函数返回 mnist 数据集图片像素值

在 mnist 数据集中，若想要查看训练集中第 0 张图片像素值，则使用如下函数 `mnist.train.images[0]`
输出结果：
```
array([0. ,0. ,0. ,
       1. ,0. ,0. ,
       2. ,0. ,0. ,
       …   …   …])
```
使用 mnist.train.next_batch()函数将数据输入神经网络
``` python
BATCH_SIZE = 200
xs,ys = mnist.train.next_batch(BATCH_SIZE)
print "xs shape:",xs.shape 
print "ys shape:",ys.shape 
```
```
输出结果：xs.shape(200,784)
输出结果：ys.shape(200,10)
```
其中，`mnist.train.next_batch() `函数包含一个参数 `BATCH_SIZE`，表示随机从训练集中抽取 `BATCH_SIZE` 个样本输入神经网络，并将样本的像素值和标签分别赋给 `xs` 和 `ys`。

在本例中，`BATCH_SIZE`设置为 200，表示一次将 200 个样本的像素值和标签分别赋值给 `xs` 和 `ys`，故 `xs `的形状为(200,784)，对应的 `ys `的形状为 (200,10)。

## "Mnist 数据集手写数字识别"的常用函数：

 + `tf.get_collection(" ") `函数表示从`collection` 集合中取出全部变量生成一个列表。

+v tf.add( ) 函数表示将参数列表中对应元素相加。

```
x=tf.constant([[1,2],[1,2]])
y=tf.constant([[1,1],[1,2]]) 
z=tf.add(x,y)
print z
```
输出结果：` [[2,3],[2,4]]`

③ tf.cast(x,dtype) 函数表示将参数 x 转换为指定数据类型。
【例如】：

A = tf.convert_to_tensor(np.array([[1,1,2,4], [3,4,8,5]])) print A.dtype
b = tf.cast(A, tf.float32) print b.dtype
结果输出：

<dtype: 'int64'>
<dtype: 'float32'>
从输出结果看出，将矩阵 A 由整数型变为 32 位浮点型。

④ tf.equal( ) 函数表示对比两个矩阵或者向量的元素。
若对应元素相等，则返回 True；若对应元素不相等，则返回 False。
【例如】：

A = [[1,3,4,5,6]]
B = [[1,3,4,3,2]]

with tf.Session( ) as sess: 
    print(sess.run(tf.equal(A, B)))
输出结果：[[ True True True False False]]

在矩阵 A 和 B 中，第 1、2、3 个元素相等，第 4、5 个元素不等，故输出结果中，第 1、2、3 个元素取值为 True，第 4、5 个元素取值为 False。

+ `tf.reduce_mean(x,axis)` 函数表示求取矩阵或张量指定维度的平均值。
+ 
若不指定第二个参数，则在所有元素中取平均值；

若指定第二个参数为 0，则在第一维元素上取平均值，即每一列求平均值；

若指定第二个参数为 1，则在第二维元素上取平均值，即每一行求平均值。

```

x = [[1., 1.]
    [2., 2.]]
print(tf.reduce_mean(x))
```
输出结果：`1.5`

`print(tf.reduce_mean(x, 0))`

输出结果：`[1.5, 1.5]`

`print(tf.reduce_mean(x, 1))`

输出结果：`[1., 1.]`

+ `tf.argmax(x,axis)` 函数表示返回指定维度 `axis` 下，参数 `x `中最大值索引号。


在 `tf.argmax([1,0,0],1)` 函数中，`axis` 为 1，参数 `x` 为 `[1,0,0]`，表示在参数 x的第一个维度取最大值对应的索引号，故返回 0。

+ `os.path.join()` 函数表示把参数字符串按照路径命名规则拼接。

```
import os
os.path.join('/hello/','good/boy/','doiido')
```
输出结果：`'/hello/good/boy/doiido'`

+ 字符串`.split( ) `函数表示按照指定"拆分符"对字符串拆分，返回拆分列表。

```
'./model/mnist_model-1001'.split('/')[-1].split('-')[-1]
```
在该例子中，共进行两次拆分。
第一个拆分符为 /，返回拆分列表，并提取 列表中索引为 -1 的元素即倒数第一个元素；

第二个拆分符为 -，返回拆分列表，并提取列表中索引为 -1 的元素即倒数第一个元素，故函数返回值为 1001。

+ `tf.Graph( ).as_default( )` 函数表示将当前图设置成为默认图，并返回一个上下文管理器。该函数一般与 `with` 关键字搭配使用，应用于将已经定义好的神经网络在计算图中复现。

`with tf.Graph().as_default() as g`，表示将在 `Graph()` 内定义的节点加入到计算图 g 中。

## 神经网络模型的保存

在反向传播过程中，一般会间隔一定轮数保存一次神经网络模型，并产生三个文件
(保存当前图结构的 `.meta `文件、保存当前参数名的 `.index `文件、保存当前参数的 `.data `文件)，
在 Tensorflow 中如下表示：
``` python
saver = tf.train.Saver() 
with tf.Session() as sess:
    for i in range(STEPS):
        if i % 轮 数 == 0:
            saver.save(
                sess, 
                os.path.join(MODEL_SAVE_PATH, MODEL_NAME), 
                global_step=global_step
            )
```
其中，`tf.train.Saver() `用来实例化` saver` 对象。上述代码表示，神经网络每循环规定的轮数，将神经网络模型中所有的参数等信息保存到指定的路径中，并在存放网络模型的文件夹名称中注明保存模型时的训练轮数。

## 神经网络模型的加载
在测试网络效果时，需要将训练好的神经网络模型加载， 在 Tensorflow 中这样表示：
``` python
with tf.Session() as sess:
    ckpt = tf.train.get_checkpoint_state(存储路径)
    if ckpt and ckpt.model_checkpoint_path:
        saver.restore(sess, ckpt.model_checkpoint_path)
```

在 with 结构中进行加载保存的神经网络模型，若 ckpt 和保存的模型在指定路径中存在，则将保存的神经网络模型加载到当前会话中。

## 神经网络模型准确率评估方法
在网络评估时，一般通过计算在一组数据上的识别准确率，评估神经网络的效果。 在 Tensorflow 中这样表示：
```
correct_prediction = tf.equal(tf.argmax(y, 1), tf.argmax(y_, 1)) 
accuracy = tf.reduce_mean(tf.cast(correct_prediction, tf.float32)) 
```
在上述中，y 表示在一组数据(即 `batch_size `个数据)上神经网络模型的预测结果，y 的形状为 `[batch_size,10]`，每一行表示一张图片的识别结果。

通过 `tf.argmax()` 函数取出每张图片对应向量中最大值元素对应的索引值，组成长度为输入数据 `batch_size `个的一维数组。

通过 `tf.equal()` 函数判断预测结果张量和实际标签张量的每个维度是否相等，若相等则返回 True，不相等则返回 False。

通过 `tf.cast() `函数将得到的布尔型数值转化为实数型，

再通过`tf.reduce_mean()` 函数求平均值，最终得到神经网络模型在本组数据上的准确率。

## 前向传播过程(forward.py)

``` python
def forward(x, regularizer):
    w=
    b= 
    y=
    return y

def get_weight(shape, regularizer): 

def get_bias(shape):
```

前向传播过程中，需要定义神经网络中的参数 w 和偏置 b，定义由输入到输出的网络结构。
通过定义函数` get_weight()` 实现对参数 w 的设置，包括参数 w 的形状和是否正则化的标志。同样，通过定义函数 `get_bias()` 实现对偏置 b 的设置。

反向传播过程(backword.py)

``` python
def backward( mnist ):
    x  = tf.placeholder(dtype, shape )
    y_ = tf.placeholder(dtype, shape )

    # 定义前向传播函数
    y = forward( ) 
    global_step =
    loss =

    train_step = tf.train.GradientDescentOptimizer(learning_rate).minimize(loss, global_step=global_step)
    
    # 实例化 saver 对象
    saver = tf.train.Saver() 
    with tf.Session() as sess:
        # 初始化所有模型参数
        tf.initialize_all_variables().run()

        # 训练模型
        for i in range(STEPS):
            sess.run(train_step, feed_dict={x: , y_: })
            if i % 轮数 == 0:
                print
                saver.save( )
```


反向传播过程中， 用 `tf.placeholder(dtype, shape)` 函数实现训练样本 x 和样本标签 y_占位，

函数参数 `dtype` 表示数据的类型，`shape` 表示数据的形状；
y 表示定义的前向传播函数 `forward`；

`loss` 表示定义的损失函数，一般为预测值与样本标签的交叉熵(或均方误差)与正则化损失之和；
`train_step` 表示利用优化算法对模型参数进行优化 常用优化算法 `GradientDescentOptimizer`、`AdamOptimizer`、`MomentumOptimizer` 算法， 在上述代码中使用的 `GradientDescentOptimizer `优化算法。

接着实例化 `saver` 对象， 其中利用 `tf.initialize_all_variables().run()` 函数实例化所有参数模型， 利用 `sess.run( ) `函数实现模型的训练优化过程，并每间隔一定轮数保存一次模型。


