# Agent 原生组件逐身份审查（2026-11-10）

本记录是候选来源和制品审查，**本地全级别镜像门禁的未解决计数为0；远端发布及日常验收另行记录**。对象为
`trip-review-agent-843d242:20261110`，image ID `sha256:f2f76598f3eda36f86223b2e61428f3490eb31f15ed7f1e0aa927bf626550d6e`，源码`843d242ff3ac5e4fe18ef6dbf3b2c6d3b51a59ce`，已核对真实backend/Dockerfile构建入口和源码标签。
同一2026-11-10 Grype数据库保留207条raw，逐artifact ID/包/版本/CVE/等级、证据和绑定见[矩阵](agent-trip4-review.json)。

复核发现并实际补齐了两个遗漏：poplib CVE-2025-15367原真实方法可发送注入命令，现已完整回补上游两行拒绝控制字符，真实安装回归通过；
ncurses CVE-2025-6141原先仍保留libtic解析器，现已在核对其唯一消费者toe后，将toe/解析器全别名和同字节副本一并移除，保留tinfo/ncurses和包数据库。
见[修复增量](corrective-delta.json)、[poplib证据](poplib-repair-validation.json)。207条raw全部保留并逐身份分类；这些评估不能替代提交后的CI、封装镜像、Registry及日常环境验收。
3项glibc实验硬化已完成组合及实际非root/default-loader验收；当前源码镜像的libc/loader/resolv与11项NPTL、45项malloc、31次独立打包执行的制品字节相同，另在当前镜像重跑9项安装后检查通过，见[结果](trip4-hardening-validation.json)。最终Dockerfile构建与源码已绑定；相关CI和目标分支结果以最终交付记录为准。

| 结论类型 | 实际边界 |
| --- | --- |
| 真实修复/固定升级 | glibc原13项回补、libstdc++ aligned-new/from_chars、libmount稳定修复、PAM完整上游修复、dash实际libc-fnmatch重建、CPython socket/HTTP、固定ACL/SQLite包。下游版本不冒充上游或Debian正式安全发行版。 |
| 组件缺失/未引入 | apt-key/bzip2recover/attr递归工具/infocmp/指定CLI；具体GCC runtime包不含相应开发模板；libudev不含sealed-journal reader；libtic仅在完成实际纠正移除的新镜像中成立。 |
| 配置/架构不适用 | Power8实现与实际amd64不符；shadow无subuid/gid映射和分配CLI；btmp必须同时核验0:43/0660及login.defs关闭未知用户记录，空文件SHA不替代权限。 |
| 执行路径不适用 | glibc glob/POSIX regex/不可信ELF加载、libsystemd sealed-journal读取、SQLite任意受损DB导入，限定当前HTTP/上传/索引路径。功能仍在，不称修复或组件缺失。Chroma4项仍走独立语言VEX。 |

证据：[制品清单](agent-trip4-artifact-inventory.json)、[实际执行路径探针](execution-path-probe.json)、
[ncurses source分组](ncurses-parser-source.json)。968个ELF全部解析成功，10042个已装Python源码无journal reader候选。
lslogins确有reader导入，logger只有发送，两者都保留。动态导入/AST检查不代表任意dlsym、静态代码或未来插件的穷尽证明。

## 源包别名逐项核对

- gcc-14身份位于libgcc_s.so.1，不代表装了compiler；其6条按真实payload审查，libstdc++6真实runtime修复另列。
- systemd身份聚合libsystemd和libudev；journal3项按reader存在的执行边界评估，不能因daemon缺失一并记组件不存在。
- 上个234条原型的shadow身份来自4个converter，新镜像确实删除后该别名2条消失；passwd/login.defs的4条仍逐条评估。
- 234→207减少27条=openssl binary23+gzip binary2+shadow source alias2。真实工具移除不删除包数据库，也不宣称保留库的全部缺陷已修复。
- util-linux有两个同名同版本artifact（包DB与实际ELF/工具集合）；12条raw保留，不错误去重成6条。

## 导出真实性纠正与失效条件

Docker cp --follow-link对部分SONAME产生假缺失，实际libtic也被误报。现已采用同一从未启动容器的
rootfs tar逐目录/链接解析核验，无宿主解包或执行镜像；5个真实SONAME可读取，libtic实际存在，原失败记录保留。
早先“删除tic即无parser”推断不成立，原失败和纠正前库证据保留；新分类必须依赖后续真实toe/libtic移除结果，不能沿用旧假缺失。

活动policy已按真实源码和制品绑定：197项vendor评估覆盖203条raw，另4条由独立Chroma VEX评估；本地阻断0、未修复风险接受0。原始207条不改成“零漏洞”，见[三镜像门禁记录](base-image-gates-843d242.json)。文档状态及公开摘要说明的后续变化见[独立绑定增量](documentation-binding-delta.json)，不改变漏洞身份、评估结论或运行条件。
execution_path须绑定冻结审查材料和真实/app/app/*.py及必要已装依赖LF哈希；
configuration还须内容SHA及架构或mode/uid/gid。正式VCS候选需再次核验入口、源码、库与包、上传类型、
固定索引目录及挂载；运营者新加journal/ELF/DB导入或插件会使相应判断失效。

首次pytest缺少contracts/已发布SQL只读夹具的5失败保留；807 passed/1 skipped/50 subtests的实际执行镜像及复用范围见[验证复用记录](validation-reuse.json)。最终排序改动另有6项直接工具测试；未把复用结果冒称最后镜像新跑全量pytest，跳过项不称通过。

清单fingerprints曾因frozenset遍历而在同源码两次构建间改变顺序。最终源码已修复排序：[实际镜像字节验证](manifest-order-validation.json)证明新清单恰等于原清单仅排序该数组后的完整序列化字节；跨进程PYTHONHASHSEED=1/99测试比较完整清单一致。策略仍要求原始文件SHA，不以归一化代替实际绑定。
一手引用逐CVE列于矩阵。实现与对应源码入口：[glibc/GCC/util-linux回补](../../../backend/security/native-backports/README.md)、
[逐patch清单](../../../backend/security/native-backports/glibc-patches.txt)、[实验硬化](../../../backend/security/glibc-hardening/STACK-RANDOMIZATION.md)、
[CPython](../../../backend/security/cpython-backports/README.md)、[PAM](../../../backend/security/pam-backport/README.md)、[dash](../../../backend/security/dash-backport/README.md)。
GCC115456→PR127666的对应仍明确属于CNA/上游症状与路径一致的推断；[官方GCC提交](https://gcc.gnu.org/pipermail/libstdc%2B%2B-cvs/2026q4/045443.html)没有CVE交叉ID，不伪造为官方确认。
所有必要许可、对应源码及真实修复材料继续保留；不声称整个系统完全安全。
