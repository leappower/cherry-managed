# SQLite 数据仓库目录

由 `db.init_db()` 在首次启动时自动创建 `managed.db`（含 5 张核心表：
devices / dispatch_log / usage_agg / agent_files / audit_log）。

WAL 模式，跨线程安全（每线程独立连接）。
