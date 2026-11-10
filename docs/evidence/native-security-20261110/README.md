# Agent 原生组件逐身份审查（2026-11-10）

本记录是候选审查，**不是发布完成或告警归零的证明**。对象为
`trip-review-agent-8182d40:20261110`，image ID `sha256:e7c9075d385cd1333e42772214849374534d7f348e00c000331e31f8ab0a7f87`，源码`8182d40757c60a9c695d3d37b9bc4c41e1a830e5`，已核对真实backend/Dockerfile构建入口和源码标签。
同一2026-11-10 Grype数据库保留207条raw，逐artifact ID/包/版本/CVE/等级、证据和绑定见[矩阵](agent-trip4-review.json)。

复核发现并实际补齐了两个遗漏：poplib CVE-2025-15367原真实方法可发送注入命令，现已完整回补上游两行拒绝控制字符，真实安装回归通过；
ncurses CVE-2025-6141原先仍保留libtic解析器，现已在核对其唯一消费者toe后，将toe/解析器全别名和同字节副本一并移除，保留tinfo/ncurses和包数据库。
见[修复增量](corrective-delta.json)、[poplib证据](poplib-repair-validation.json)。207条raw全部保留并逐身份提供候选分类；本地草稿并未代替正式源码/CI/镜像标签的最终绑定验收。
3项glibc实验硬化在本精确原型已完成组合及实际非root/default-loader验收：11项NPTL、45项malloc、31次独立打包执行及9项安装后检查通过，见[结果](trip4-hardening-validation.json)。正式VCS候选仍需用最终源码干净重建并重绑。

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

候选policy只在本地草稿，没有修改实际.github policy或刷新其source绑定。
execution_path须绑定冻结审查材料和真实/app/app/*.py及必要已装依赖LF哈希；
configuration还须内容SHA及架构或mode/uid/gid。正式VCS候选需再次核验入口、源码、库与包、上传类型、
固定索引目录及挂载；运营者新加journal/ELF/DB导入或插件会使相应判断失效。

首次pytest缺少contracts/已发布SQL只读夹具的5失败保留；补齐真实只读夹具的新镜像检查为807 passed/1 skipped/50 subtests。跳过项不称通过。
一手引用逐CVE列于矩阵。实现与对应源码入口：[glibc/GCC/util-linux回补](../../../backend/security/native-backports/README.md)、
[逐patch清单](../../../backend/security/native-backports/glibc-patches.txt)、[实验硬化](../../../backend/security/glibc-hardening/STACK-RANDOMIZATION.md)、
[CPython](../../../backend/security/cpython-backports/README.md)、[PAM](../../../backend/security/pam-backport/README.md)、[dash](../../../backend/security/dash-backport/README.md)。
GCC115456→PR127666的对应仍明确属于CNA/上游症状与路径一致的推断；[官方GCC提交](https://gcc.gnu.org/pipermail/libstdc%2B%2B-cvs/2026q4/045443.html)没有CVE交叉ID，不伪造为官方确认。
所有必要许可、对应源码及真实修复材料继续保留；不声称整个系统完全安全。
