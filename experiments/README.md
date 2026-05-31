# 试跑样例

主流程已统一为「问句话题 → 搜文 → 改编」，日常用 `./make-and-publish.sh`，手动选题用 `./make-topics.sh`。

本目录只保留**可复制的选题样例**与**只生成不发布**的入口，避免与主脚本重复维护一套逻辑。

## 只生成、不发布

```bash
# 科普向（搜文窗口 120 天）
AIVIDEO_TOPIC_DAYS=120 ./make-topics.sh --no-publish --file experiments/topics-sample.txt

# 或只跑样例里第二行热点（7 天，与线上一致）
echo "AI 为什么最近大模型价格战越来越激烈" | ./make-topics.sh --no-publish -
```

编辑 `topics-sample.txt` 后同样命令即可试新选题；满意的话题写入根目录 `topics.txt` 再 `./make-topics.sh`。

## 正式发布单条

```bash
./make-topics.sh --file topics.txt
```
