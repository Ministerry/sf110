# config.py
import os

# 从环境变量读取，或者设置默认值（仅用于开发环境）
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY', 'sk-56f89e293dcb4785bb8e3a0a1b7ffb60')
SILICONFLOW_API_KEY = os.getenv('SILICONFLOW_API_KEY','sk-pswqrymmruxnnfderizdxyxrubibdkxdqsyfcsfdsoxrxosv')