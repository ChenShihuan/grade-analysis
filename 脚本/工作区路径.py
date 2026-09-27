# -*- coding: utf-8 -*-
"""工作区路径约定：**代码在工作区，数据在 `数据/` 子目录。**

    成绩分析工作区/            ← 代码库（git 仓库，只跟踪代码与文档）
    ├─ 脚本/                    ← 全部脚本
    ├─ .agents/                 ← 流程文档
    ├─ 数据/                    ← 各场考试的数据（**不纳入版本管理**）
    │   ├─ 九年级9月考/
    │   └─ 八年级下/
    └─ AGENTS.md

所有脚本都从这里取路径。**不要在各自文件里另写 `os.path.join(工作区, 考试名)`**——
数据目录一挪、或者把数据放到另一块盘，那些地方就会全断，而且断得莫名其妙。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)                       # 成绩分析工作区/
DATA_DIR_NAME = "数据"
DATA_ENV = "成绩数据目录"


def 数据根目录():
    """各场考试目录的父目录。

    默认 `<工作区>/数据`。若把数据放在别处（另一块盘、另一个同步目录），
    设环境变量 `成绩数据目录` 指过去即可，脚本一行都不用改。
    """
    env = os.environ.get(DATA_ENV)
    return os.path.abspath(env) if env else os.path.join(WS, DATA_DIR_NAME)


def resolve_exam_dir(arg):
    """把考试名（如「九年级9月考」）解析成数据目录下的完整路径。

    直接传绝对路径就用它——数据放在别处、或只处理单个目录时走这条路。
    """
    cand = arg if os.path.isabs(arg) else os.path.join(数据根目录(), arg)
    if not os.path.isdir(cand):
        cand2 = os.path.join(os.getcwd(), arg)
        if os.path.isdir(cand2):
            cand = cand2
        else:
            raise SystemExit(
                f"找不到考试目录：{arg}\n"
                f"  已尝试：{cand}\n"
                f"  当前数据目录：{数据根目录()}\n"
                f"  （默认是 <工作区>/{DATA_DIR_NAME}；数据放在别处就设环境变量 {DATA_ENV}）")
    return os.path.abspath(cand)
