#!/usr/bin/env python3
"""泌尿外科文献雷达 · 入口。

    python main.py build            # 抓取 + 打分 + 中文速读 + 生成 public/index.html
    python main.py check            # 核对期刊池（ISSN 和 PubMed 缩写对不对）
    python main.py build --no-llm   # 不调模型，只看抓取和排序
"""

import sys

from radar.cli import main

if __name__ == "__main__":
    sys.exit(main())
