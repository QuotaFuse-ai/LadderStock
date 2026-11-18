"""通用子模块安装器：清单、状态机、执行器。

对外入口：

```python
from ladderstock.modules.installer import manifest, runner, state

runner.missing_peers()              # 还没装的 peer
runner.install_peer("tickhub", on_progress=fn)
state.read_all()                    # 六个 peer 的状态
manifest.total_mb()                 # 全选时的磁盘占用
```
"""

from ladderstock.modules.installer import manifest, runner, state

__all__ = ["manifest", "runner", "state"]
