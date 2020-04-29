# xla优化过程

## simplification

### Batch Normalization（添加BN层）

深度网络参数训练时内部存在协方差偏移（Internal Covariate Shift）现象：深度网络内部数据分布在训练过程中发生变化的现象。

训练深度网络时，神经网络隐层参数更新会导致网络输出层输出数据的分布发生变化，而且随着层数的增加，根据链式规则，这种偏移现象会逐渐被放大。这对于网络参数学习来说是个问题：因为神经网络本质学习的就是数据分布（representation learning），如果数据分布变化了，神经网络又不得不学习新的分布。为保证网络参数训练的稳定性和收敛性，往往需要选择比较小的学习速率（learning rate），同时参数初始化的好坏也明显影响训练出的模型精度，特别是在训练具有饱和非线性（死区特性）的网络，比如即采用S或双S激活函数网络，比如LSTM，GRU。

解决办法：引入Batch Normalization，作为深度网络模型的一个层，每次先对input数据进行归一化，再送入神经网络输入层。

Batch normalization解决的问题：
解决的问题是梯度消失与梯度爆炸。
关于梯度消失，以sigmoid函数为例子，sigmoid函数使得输出在[0,1]之间。
![1](https://github.com/dongbeiyewu/xla/raw/master/week4/pic/1.png)

事实上x到了一定大小，经过sigmoid函数的输出范围就很小了，参考下图
![1](https://github.com/dongbeiyewu/xla/raw/master/week4/pic/2.png)
如果输入很大，其对应的斜率就很小，我们知道，其斜率（梯度）在反向传播中是权值学习速率。所以就会出现如下的问题，
![1](https://github.com/dongbeiyewu/xla/raw/master/week4/pic/3.png)
在深度网络中，如果网络的激活输出很大，其梯度就很小，学习速率就很慢。假设每层学习梯度都小于最大值0.25，网络有n层，因为链式求导的原因，第一层的梯度小于0.25的n次方，所以学习速率就慢，对于最后一层只需对自身求导1次，梯度就大，学习速率就快。
这会造成的影响是在一个很大的深度网络中，浅层基本不学习，权值变化小，后面几层一直在学习，结果就是，后面几层基本可以表示整个网络，失去了深度的意义。

关于梯度爆炸，根据链式求导法，
第一层偏移量的梯度=激活层斜率1x权值1x激活层斜率2x…激活层斜率(n-1)x权值(n-1)x激活层斜率n
假如激活层斜率均为最大值0.25，所有层的权值为100，这样梯度就会指数增加。

对每个特征进行独立的normalization。
第j个维度，传入m个训练样本
仅仅使用上面的归一化公式，对网络某一层A的输出数据做归一化，然后送入网络下一层B，这样是会影响到本层网络A所学习到的特征的
引入了可学习参数γ、β

对于神经网络中的第L层

## algsimp优化
## zero_sized_hlo_eliminatioin优化

## dynamic-index-splitter优化

## gpu_hlo_support_checker

## CallInliner 优化

## dot_decomposer优化

## convolution-group-converter优化

## stable-sort-expander

##  element_type_converter

## simplification
