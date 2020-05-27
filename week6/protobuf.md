protocolbuffer(以下简称PB)是google 的一种数据交换的格式，它独立于语言，独立于平台。google 提供了多种语言的实现：java、c#、c++、go 和 python，每一种实现都包含了相应语言的编译器以及库文件。由于它是一种二进制的格式，比使用 xml 进行数据交换快许多。可以把它用于分布式应用之间的数据通信或者异构环境下的数据交换。作为一种效率和兼容性都很优秀的二进制数据传输格式，可以用于诸如网络传输、配置文件、数据存储等诸多领域

## ProtoBuf协议说明

proto文件定义了协议数据中的实体结构(message ,field)

+ 关键字message: 代表了实体结构，由多个消息字段(field)组成。
+ 消息字段(field): 包括数据类型、字段名、字段规则、字段唯一标识、默认值
+ 数据类型：如下图所示
+ 字段规则：
>required：必须初始化字段，如果没有赋值，在数据序列化时会抛出异常
optional：可选字段，可以不必初始化。
repeated：数据可以重复(相当于java 中的Array或List)
字段唯一标识：序列化和反序列化将会使用到。

## ProtoBuf的使用流程
### 简单例子的描述
该程序由两部分组成。第一部分被称为 Writer，第二部分叫做 Reader。

Writer 负责将一些结构化的数据写入一个磁盘文件，Reader 则负责从该磁盘文件中读取结构化数据并打印到屏幕上。

准备用于演示的结构化数据是 HelloWorld，它包含两个基本数据：

+ ID，为一个整数类型的数据
+ Str，这是一个字符串
### 书写 .proto 文件
首先编写一个 proto 文件，定义程序中需要处理的结构化数据，在 protobuf 的术语中，结构化数据被称为 Message。proto 文件非常类似 java 或者 C 语言的数据定义。下面代码显示了例子应用中的 proto 文件内容：
```
package lm; 
message helloworld 
{ 
   required int32     id = 1;  // ID 
   required string    str = 2;  // str 
   optional int32     opt = 3;  //optional field 
}
```
在上例中，package 名字叫做 lm，定义了一个消息 helloworld，该消息有三个成员，类型为 int32 的 id，另一个为类型为 string 的成员 str。opt 是一个可选的成员，即消息中可以不包含该成员。

将命名规则定于如下：
`packageName.MessageName.proto`
## 编译.proto文件
写好 proto 文件之后就可以用 Protobuf 编译器将该文件编译成目标语言了。可以根据不同的语言来选择不同的编译方式,本例中使用 C++。
假设 proto 文件存放在 $SRC_DIR 下面，同时把生成的文件放在同一个目录下，则可以使用如下命令：
```
protoc -I=$SRC_DIR --cpp_out=$DST_DIR $SRC_DIR/addressbook.proto

```
命令将生成两个文件：

`lm.helloworld.pb.h` ， 定义了 C++ 类的头文件

`lm.helloworld.pb.cc` ， C++ 类的实现文件

在生成的头文件中，定义了一个 C++ 类 helloworld，后面的 Writer 和 Reader 将使用这个类来对消息进行操作。诸如对消息的成员进行赋值，将消息序列化等等都有相应的方法。

### 编写 writer 和 Reader
如前所述，Writer 将把一个结构化数据写入磁盘，以便其他人来读取。假如不使用 Protobuf，其实也有许多的选择。一个可能的方法是将数据转换为字符串，然后将字符串写入磁盘。转换为字符串的方法可以使用 sprintf()，这非常简单。数字 123 可以变成字符串”123”。

这样的做法对写 Reader 的那个人的要求比较高，Reader 的作者必须了 Writer 的细节。比如”123”可以是单个数字 123，但也可以是三个数字 1,2 和 3，等等。这么说来，还必须让 Writer 定义一种分隔符一样的字符，以便 Reader 可以正确读取。但分隔符也许还会引起其他的什么问题。最后发现一个简单的 Helloworld 也需要写许多处理消息格式的代码。

如果使用 Protobuf，那么这些细节就可以不需要应用程序来考虑了。

使用 Protobuf，Writer 的工作很简单，需要处理的结构化数据由 .proto 文件描述，经过以上的编译过程后，该数据化结构对应了一个 C++ 的类，并定义在 lm.helloworld.pb.h 中。对于本例，类名为 lm::helloworld。

Writer 需要 include 该头文件，然后便可以使用这个类了。

现在，在 Writer 代码中，将要存入磁盘的结构化数据由一个 lm::helloworld 类的对象表示，它提供了一系列的 get/set 函数用来修改和读取结构化数据中的数据成员，或者叫 field。

当我们需要将该结构化数据保存到磁盘上时，类 lm::helloworld 已经提供相应的方法来把一个复杂的数据变成一个字节序列，可以将这个字节序列写入磁盘。

对于想要读取这个数据的程序来说，也只需要使用类 lm::helloworld 的相应反序列化方法来将这个字节序列重新转换会结构化数据。同开始时那个“123”的想法类似，不过 Protobuf 想的远远比那个粗糙的字符串转换要全面。

 Writer 的主要代码
 ``` c++
 #include "lm.helloworld.pb.h"
…
 
 int main(void) 
 { 
   
  lm::helloworld msg1; 
  msg1.set_id(101); 
  msg1.set_str(“hello”); 
     
  // Write the new address book back to disk. 
  fstream output("./log", ios::out | ios::trunc | ios::binary); 
         
  if (!msg1.SerializeToOstream(&output)) { 
      cerr << "Failed to write msg." << endl; 
      return -1; 
  }         
  return 0; 
 }
 ```
 Msg1 是一个 helloworld 类的对象，set_id() 用来设置 id 的值。SerializeToOstream 将对象序列化后写入一个 fstream 流。

 reader 的主要代码。
``` c++
#include "lm.helloworld.pb.h" 
…
 void ListMsg(const lm::helloworld & msg) { 
  cout << msg.id() << endl; 
  cout << msg.str() << endl; 
 } 
  
 int main(int argc, char* argv[]) { 
 
  lm::helloworld msg1; 
  
  { 
    fstream input("./log", ios::in | ios::binary); 
    if (!msg1.ParseFromIstream(&input)) { 
      cerr << "Failed to parse address book." << endl; 
      return -1; 
    } 
  } 
  
  ListMsg(msg1); 
  … 
 }
 ```
运行 Writer 和 Reader 的结果如下：

```
>writer 
>reader 
101 
Hello
```

### 序列化和反序列化

```Java
public class Test {
    public static void main(String[] args) throws IOException {
        //模拟将对象转成byte[]，方便传输
        PersonEntity.Person.Builder builder = PersonEntity.Person.newBuilder();
        builder.setId(1);
        builder.setName("ant");
        builder.setEmail("ghb@soecode.com");
        PersonEntity.Person person = builder.build();
        System.out.println("before :"+ person.toString());

        System.out.println("===========Person Byte==========");
        for(byte b : person.toByteArray()){
            System.out.print(b);
        }
        System.out.println();
        System.out.println(person.toByteString());
        System.out.println("================================");

        //模拟接收Byte[]，反序列化成Person类
        byte[] byteArray =person.toByteArray();
        Person p2 = Person.parseFrom(byteArray);
        System.out.println("after :" +p2.toString());
    }
}
```
protobuf的使用过程可以分为以下三个，准备好数据，通过build()方法来组装成protobuf包，然后通过toByteArray()来将protobuf转换成二进制序列流文件（序列化）。
反序列化的过程刚好与之相反，接收到的二进制数据转换成二进制数组byte[]，然后调用protobuf的parseFrom()方法即可实现反序列化。

![](https://github.com/dongbeiyewu/xla/raw/master/week6/pic/2.png)

## protoBuf数据协议的优势
+ 平台无关，语言无关，可扩展；
+ 提供了友好的动态库，使用简单；
+ 解析速度快，比对应的XML快约20-100倍；
+ 序列化数据非常简洁、紧凑，与XML相比，其序列化之后的数据量约为1/3到1/10。

它有一个非常棒的特性，即“向后”兼容性好，人们不必破坏已部署的、依靠“老”数据格式的程序就可以对数据结构进行升级。这样您的程序就可以不必担心因为消息结构的改变而造成的大规模的代码重构或者迁移的问题。因为添加新的消息中的 field 并不会引起已经发布的程序的任何改变。

Protobuf 语义更清晰，无需类似 XML 解析器的东西（因为 Protobuf 编译器会将 .proto 文件编译生成对应的数据访问类以对 Protobuf 数据进行序列化、反序列化操作）。

数据量小是因为，Protobuf 序列化后所生成的二进制消息非常紧凑，这得益于 Protobuf 采用的非常巧妙的little-endian编码方法。

转换速度快。XML 需要从文件中读取出字符串，再转换为 XML 文档对象结构模型。之后，再从 XML 文档对象结构模型中读取指定节点的字符串，最后再将这个字符串转换成指定类型的变量。这个过程非常复杂，其中将 XML 文件转换为文档对象结构模型的过程通常需要完成词法文法分析等大量消耗 CPU 的复杂计算。

反观 Protobuf，它只需要简单地将一个二进制序列，按照指定的格式读取到 C++ 对应的结构类型中就可以了。从上一节的描述可以看到消息的 decoding 过程也可以通过几个位移操作组成的表达式计算即可完成。速度非常快。

