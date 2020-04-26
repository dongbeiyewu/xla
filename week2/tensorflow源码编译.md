# tensorflow源码编译

## 准备工作

### 安装 TensorFlow pip 软件包依赖项

>`pip install -U --user pip six numpy wheel setuptools mock 'future>=0.17.1'`</br>
    `pip install -U --user keras_applications --no-deps`</br>
    `pip install -U --user keras_preprocessing --no-deps`

### 安装 Bazel

步骤1：将Bazel发行版URI添加为包源

>`sudo apt install curl`</br>
`curl https://bazel.build/bazel-release.pub.gpg | sudo apt-key add -
echo "deb [arch=amd64] https://storage.googleapis.com/bazel-apt stable jdk1.8" | sudo tee /etc/apt/sources.list.d/bazel.list`

步骤2：安装和更新Bazel

>`sudo apt update && sudo apt install bazel`

安装后，可以在常规系统更新中升级到Bazel的较新版本：

>`sudo apt update && sudo apt full-upgrade`

该bazel软件包将始终安装Bazel的最新稳定版本。除了最新版本的Bazel之外，还可以安装特定版本的Bazel：

>`sudo apt install bazel-1.0.0`

步骤3：安装JDK

>`# Ubuntu 16.04 (LTS) uses OpenJDK 8 by default:`</br>
`sudo apt install openjdk-8-jdk`
</br>

### 使用二进制安装程序

可以从Bazel的[GitHub版本页面](https://github.com/bazelbuild/bazel/releases)下载二进制安装程序。

tensorflow1.14版本应使用bazel0.25.2版本进行编译

步骤1：安装所需的软件包
Bazel需要一个C ++编译器并解压缩才能工作：

>`sudo apt install g++ unzip zip`

步骤2：运行安装程序

>`./bazel-<version>-installer-linux-x86_64.sh --user`


## 下载 TensorFlow 源代码

>`  git clone https://github.com/tensorflow/tensorflow.git`
</br>
>` cd tensorflow`


也可以直接下载（推荐）

>`wget https://github.com/tensorflow/tensorflow/archive/r1.14.zip`

解压

>`unzip r1.14.zip`

配置 build:通过运行 TensorFlow 源代码树根目录下的 ./configure 配置系统 build。此脚本会提示指定 TensorFlow 依赖项的位置，并要求指定其他构建配置选项（例如，编译器标记）。

> `./configure`

打开xla编译开关

![xla](https://github.com/erguixieshen/XLA/raw/master/week2/picture/1.png)

### Bazel build

>`bazel build --config=opt //tensorflow/tools/pip_package:build_pip_package`

### 编译TensorFlow  SHA256校验错误临时解决方案

开始利用bazel编译时Kaijia碰到了不应该出现的包括protobuf和llvm等数个组件下载后SHA256与期望SHA256存在差异的校验问题。

按照相关的信息连接到Git的问题系统可以发现在最新的libgit2版本v0.26.0更改了生成压缩包的方法，因此出现了校验码的变动。在GitHub更新了libgit2之后，原先在tensorflow/workspace.bzl定义通过GitHub直接下载的protobuf和llvm等库变出现了因为校验码变化而无法通过验证的问题。

目前有的临时解决方案主要为替换新的SHA256、替换库文件下载地址以及直接禁用SHA256校验

>`sed -i -e 's/00fb4a83a4dd1c046b19730a80e2183acc647715b7a8dcc8e808d49ea5530ca8/a8da6d42ac7419e543a27e405f8b660f7b065e9ba981cc9cdcdcecb81af9cc43/g' tensorflow/workspace.bzl`
</br>
`sed -i '\@https://github.com/google/protobuf/archive/0b059a3d8a8f8aa40dde7bea55edca4ec5dfea66.tar.gz@d' tensorflow/workspace.bzl`
</br>
`bazel clean`
</br>
`bazel build --config=opt //tensorflow/tools/pip_package:build_pip_package`

编译成功

### Build软件包

bazel build 命令创建名为 build_pip_package 的可执行程序，这个程序用于构建 pip 包。请执行以下命令在 /tmp/tensolflow_pkg 目录下创建一个 .whl 包。

- 从release分支build:
>`./bazel-bin/tensorflow/tools/pip_package/build_pip_package /tmp/tensorflow_pkg`

- 从master分支build则需要使用 --nightly_flag 以获得正确的依赖:

>`./bazel-bin/tensorflow/tools/pip_package/build_pip_package --nightly_flag /tmp/tensorflow_pkg`

### 安装软件包

>`pip install /tmp/tensorflow_pkg/tensorflow-1.14-cp35-cp35m-linux_x86_64.whl`

通过tensorflow源码安装成功！




