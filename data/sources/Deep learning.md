# Deep learning · 资料摘要

收集日期：2026-10-23

> 说明：这篇 "Deep learning" 不是专著，而是 LeCun、Bengio、Hinton 三人 2015 年发表在 Nature 上的 8 页综述（Nature 521: 436–444，DOI 10.1038/nature14539），属 Nature 纪念 AI 诞生 60 周年的专题。以下资料只做摘要，不含原文段落。

## 简介

- 主题：用多层神经网络从原始数据中自动学习分层表示（representation learning），取代人工设计特征。
- 出版背景：2015 年 5 月 27 日上线，Nature 为 1956 年达特茅斯会议（AI 诞生）60 周年组织的专题之一。三位作者首次联合署名。
- 影响：Nature 页面显示百万级访问、数万次引用，是深度学习被主流科学界接纳的标志性文本；三人因深度学习获 2018 年图灵奖。
- 定位：综述 + 立场宣言，不是教材，不提供完整数学推导。

## 作者背景

- Yann LeCun：NYU / Facebook AI Research。卷积网络与 LeNet 的开创者，长期研究能量模型、自监督学习。
- Yoshua Bengio：蒙特利尔大学。神经概率语言模型、词向量、注意力机制的早期推动者，代表作为 representation learning 综述。
- Geoffrey Hinton：多伦多大学 / Google。反向传播普及、玻尔兹曼机、Dropout、深度信念网络预训练；AlexNet 的指导者。
- 三人共同立场：智能的关键是表示，表示应该从数据中学，而不是由人写规则。

## 章节骨架（按原文小节顺序）

1. Introduction：数据获取容易、标注与特征设计困难；主张学习多层表示。
2. Supervised learning：监督学习的基本框架（打分函数、损失、随机梯度下降、小批量、正则化）；Box 1 讲表示学习。
3. Backpropagation to train multilayer architectures：链式法则求梯度；深层网络难训的原因（梯度消失、病态条件、局部极小/鞍点）与工程解法（ReLU、初始化、动量、Dropout、预训练）。
4. Convolutional neural networks：局部连接、权值共享、池化；特征图与层级组合。
5. Image understanding with deep convolutional networks：ImageNet 分类突破、检测、分割、图像描述。
6. Distributed representations and language processing：词向量、神经语言模型、机器翻译、情感分类、问答。
7. Recurrent neural networks：沿时间展开、参数共享、长短期记忆网络、语音与文本应用、注意力。
8. Concluding remarks：无监督学习、深度强化学习、自然语言理解、记忆与推理；"下一步"类问题尚未解决。

（骨架来源：Nature 官网摘要与章节/图注列表、Ovid 摘要页、作者主页 PDF 的目录结构；仅取结构信息。）

## 核心概念

- 表示学习：模型自己学出该用哪些特征，而不是人来设计。
- 分布式表示：一个概念由许多单元共同表示，每个单元携带一点语义，组合数随单元数指数增长。
- 反向传播：用链式法则从损失往回算每个参数的梯度。
- 随机梯度下降：每次用一小批样本估计梯度并更新参数。
- 端到端学习：从原始输入直接学到输出，中间不插人工模块。
- 卷积神经网络：局部连接 + 权值共享 + 池化，专用于网格状数据（图像、音频频谱）。
- 循环神经网络：同一网络沿时间重复使用，内部状态携带历史信息。
- 梯度消失：误差反传时逐层相乘导致前层梯度趋近于零。
- 长短期记忆网络：用输入门、遗忘门、输出门控制记忆的读写，缓解长程依赖问题。
- 预训练：先用无标注数据学一个初始表示，再用标注数据微调。
- Dropout：训练时随机失活部分单元，近似模型集成、抑制过拟合。

## 书评与争议点

- 赞扬：定义清晰、把散落的技术串成一条主线；三位作者身份使它在科学界具有"官方定调"的作用；对卷积与循环两类结构先验的概括成为后续教学的标准框架。
- 批评一：选择性举证。以成功案例为主，对失败案例、计算成本、数据依赖着墨很少。
- 批评二：低估局限。缺少对推理、因果、组合泛化、可解释性的讨论；Gary Marcus 2018 年的 "Deep Learning: A Critical Appraisal" 系统列出十项担忧，主张深度网络必须与其他技术结合。
- 批评三：预测偏差。文中认为无监督学习长期更重要，但此后真正起飞的是大规模自监督 + Transformer 路线，文中强调的生成式/能量式无监督方法并未成为主流。
- 常见误读：把"深度学习"等同于"层数越多越好"。原文强调的是表示学习与结构先验，深度只是实现分层抽象的手段。
- 时代局限：2015 年的证据截止于 AlexNet、LSTM、神经机器翻译；Transformer、GPT 系列、扩散模型均未出现，注意力只被简要提及。

## 相关著作

- Rosenblatt,《The Perceptron》(1958)：单层学习机器的起点。
- Minsky & Papert,《Perceptrons》(1969)：指出单层网络无法解决异或，引发神经网络第一次低潮。
- Rumelhart, Hinton & Williams (1986)：反向传播用于多层网络的经典工作。
- LeCun et al. (1989/1998)：LeNet 系列，卷积网络原型。
- Hochreiter & Schmidhuber (1997)：长短期记忆网络。
- Hinton, Osindero & Teh (2006)：深度信念网络逐层预训练，重启深网络研究。
- Krizhevsky, Sutskever & Hinton (2012)：AlexNet，ImageNet 突破。
- Goodfellow, Bengio & Courville,《Deep Learning》(2016)：同一学派把本综述扩展为教科书。
- Lake et al. (2017), "Building Machines That Learn and Think Like People"：主张加入先验、因果与直觉物理。
- Marcus (2018), "Deep Learning: A Critical Appraisal"：对深度学习的十点批评。

## 补充（问答中收集）

### 循环网络的「参数随时间共享」具体指什么（2026-10-23）

- 展开计算图：循环图按时间复制成 T 份前馈链，各副本的输入权重 U、循环权重 W、输出权重 V 绑定为同一组值；前向为 h_t = tanh(Ux_t + Wh_{t-1} + b)，y_t = Vh_t。
- BPTT：在展开后的前馈图上做反向传播，再把各时间步对同一参数的梯度相加。
- 共享的理由不只是省参数：若每个序列长度用一套独立参数，模型无法泛化到未见长度；共享使「同一规则适用于任意位置」，等价于时间平移不变假设，也让每个位置的样本共同训练同一组参数。
- 代价：梯度是 T 项之和，链式相乘导致消失/爆炸；h_t 依赖 h_{t-1}，训练无法沿时间并行。工程上用截断 BPTT、梯度裁剪缓解；结构上用门控与注意力缓解。
- 共享只发生在同一层的时间维：堆叠循环网络每层各一套参数，双向网络两个方向各一套。
- 来源摘要：Pitt CS3750 RNN/LSTM 讲义；arXiv 2304.11461（RNN 与 LSTM 教程）；Springer《RNNs: Architectures, Training Tricks》；Dive into Deep Learning 循环网络章节。
