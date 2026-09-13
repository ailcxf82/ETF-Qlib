# 项目约定

## ETF Qlib 数据路径

- 用户于 2026-09-13 指定 ETF 数据路径，原始消息为 `D:\qlib\_data\etf\_qlib\_data`。
- 按消息中 `\_` 为下划线转义理解，实际路径为 `D:\qlib_data\etf_qlib_data`；本机已确认该目录存在。
- 原始消息按字面解释的多级目录 `D:\qlib\_data\etf\_qlib\_data` 在核对时不存在；若用户明确要求按字面使用，应再次核对。
- 后续 ETF 数据检查、适配层配置和回测使用上述已核实路径。当前 `.env`、`.env.example` 与环境检查脚本的默认路径已经一致。
