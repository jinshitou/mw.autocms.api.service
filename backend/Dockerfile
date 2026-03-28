FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
# 删除了 -i 清华源的部分，让香港服务器直接连全球官方源
RUN pip install --no-cache-dir -r requirements.txt
COPY . .