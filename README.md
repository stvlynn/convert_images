# 图片上传工具

这是一个自动将 Markdown 文档中的本地图片转换为 WebP 格式并上传到 S3 兼容存储的工具，然后更新文档中的图片链接。

## 功能特点

- 自动扫描根目录下的所有 Markdown 文件（排除 upload_images 目录本身）
- 将本地图片转换为 WebP 格式以减小体积
- 上传图片到 S3 兼容的对象存储
- 自动更新 Markdown 文件中的图片链接
- 支持断点续传，可以从上次处理的位置继续
- 可配置是否删除原始图片
- 可自定义图床路径前缀

## 环境要求

- Python 3.6+
- 虚拟环境（推荐）

## 安装步骤

1. 克隆或下载此仓库
2. 创建并激活虚拟环境：

```bash
python3 -m venv venv
source venv/bin/activate
```

3. 安装依赖：

```bash
pip install -r requirements.txt
```

4. 复制 `.env.example` 为 `.env` 并填写配置：

```bash
cp .env.example .env
```

## 配置说明

在 `.env` 文件中配置以下参数：

```
# S3配置
S3_ENDPOINT=https://your-endpoint.com
S3_ACCESS_KEY=your-access-key
S3_SECRET_KEY=your-secret-key
S3_BUCKET=your-bucket
CDN_DOMAIN=https://your-cdn-domain.com
# 图片路径前缀，默认为img
IMAGE_PATH_PREFIX=img
# 上传后是否删除原图，true或false
DELETE_ORIGINAL_IMAGES=false
```

## 使用方法

1. 确保已正确配置 `.env` 文件
2. 运行脚本：

```bash
./run.sh
```

3. 根据提示选择是否从之前的记录开始

## 工作原理

1. 脚本会遍历根目录下的所有 Markdown 文件（排除 upload_images 目录本身）
2. 对于每个文件，查找其中的图片链接（支持 Markdown 和 HTML 格式）
3. 将本地图片转换为 WebP 格式
4. 上传到 S3 存储，路径结构为：`图床前缀/文档相对于根目录的路径/md5值.webp`
5. 更新 Markdown 文件中的图片链接
6. 记录处理进度，支持断点续传

## 注意事项

- 远程图片（以 http:// 或 https:// 开头的链接）会被跳过
- 已经指向 CDN 域名的图片链接会被跳过
- 进度信息保存在 `conversion_progress.yaml` 文件中
- 日志信息保存在 `image_conversion.log` 文件中

## 许可证

MIT 