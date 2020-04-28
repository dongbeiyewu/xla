# xla优化过程

## simplification

### Batch Normalization（添加BN层）

深度网络参数训练时内部存在协方差偏移（Internal Covariate Shift）现象：深度网络内部数据分布在训练过程中发生变化的现象。

训练深度网络时，神经网络隐层参数更新会导致网络输出层输出数据的分布发生变化，而且随着层数的增加，根据链式规则，这种偏移现象会逐渐被放大。这对于网络参数学习来说是个问题：因为神经网络本质学习的就是数据分布（representation learning），如果数据分布变化了，神经网络又不得不学习新的分布。为保证网络参数训练的稳定性和收敛性，往往需要选择比较小的学习速率（learning rate），同时参数初始化的好坏也明显影响训练出的模型精度，特别是在训练具有饱和非线性（死区特性）的网络，比如即采用S或双S激活函数网络，比如LSTM，GRU。

解决办法：引入Batch Normalization，作为深度网络模型的一个层，每次先对input数据进行归一化，再送入神经网络输入层。

Batch normalization实现：

1、使网络某一层的输入样本做白化处理（最后发现等价于零均值化（Normalization）处理，拥有零均值，方差为1），输入样本之间不相关。通过零均值化每一层的输入，使每一层拥有服从相同分布的输入样本，因此克服内部协方差偏移的影响。

$$ {x^{(k)}}  = \frac{x^{(k)}-E[x^{(k)}]}{\sqrt{Var[x^{(k)}]}}$$

E（X）是输入样本X的期望，Var是输入样本X的方差。注意，对于一个d维的输入样本X=（x1,x2,....xd），要对某一层所有的维度一起进行零均值化处理，计算量大，且部分地方不可导，因此，这里的是针对每个维度k分别处理。

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
