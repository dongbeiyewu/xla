# NumPy
## NumPy安装

### 使用pip安装
>`python -m pip install --user numpy scipy matplotlib ipython jupyter pandas sympy nose`

### linux安装

>`sudo apt-get install python-numpy python-scipy python-matplotlib ipython ipython-notebook python-pandas python-sympy python-nose`

### 安装验证

```
>>> from numpy import *
>>> eye(4)
array([[1., 0., 0., 0.],
       [0., 1., 0., 0.],
       [0., 0., 1., 0.],
       [0., 0., 0., 1.]])
```
## NumPy Ndarray对象

NumPy 最重要的一个特点是其 N 维数组对象 ndarray，它是一系列同类型数据的集合，以 0 下标为开始进行集合中元素的索引。

ndarray 对象是用于存放同类型元素的多维数组。

ndarray 中的每个元素在内存中都有相同存储大小的区域。

ndarray 内部由以下内容组成：
+ 一个指向数据（内存或内存映射文件中的一块数据）的指针。

+ 数据类型或 dtype，描述在数组中的固定大小值的格子。

+ 一个表示数组形状（shape）的元组，表示各维度大小的元组。

+ 一个跨度元组（stride），其中的整数指的是为了前进到当前维度下一个元素需要"跨过"的字节数。

ndarray 的内部结构:

![1](https://github.com/erguixieshen/XLA/raw/master/week3/1.png)
