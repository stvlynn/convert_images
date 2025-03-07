import os
import re
import hashlib
from PIL import Image
import boto3
from botocore.client import Config
import requests
from pathlib import Path
import io
import yaml
from tqdm import tqdm
import time
import logging
from dotenv import load_dotenv
import shutil

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('image_conversion.log'),
        logging.StreamHandler()
    ]
)

# S3配置
S3_ENDPOINT = os.getenv('S3_ENDPOINT')
S3_ACCESS_KEY = os.getenv('S3_ACCESS_KEY')
S3_SECRET_KEY = os.getenv('S3_SECRET_KEY')
S3_BUCKET = os.getenv('S3_BUCKET')
CDN_DOMAIN = os.getenv('CDN_DOMAIN', '').rstrip('/')  # 移除末尾的斜杠，避免路径问题
IMAGE_PATH_PREFIX = os.getenv('IMAGE_PATH_PREFIX', 'img')  # 图片路径前缀，默认为img
DELETE_ORIGINAL_IMAGES = os.getenv('DELETE_ORIGINAL_IMAGES', 'false').lower() == 'true'  # 是否删除原图
IMAGE_FORMAT = os.getenv('IMAGE_FORMAT', 'original').lower()  # 图片格式，默认保留原格式

# 验证必要的环境变量
required_env_vars = ['S3_ENDPOINT', 'S3_ACCESS_KEY', 'S3_SECRET_KEY', 'S3_BUCKET', 'CDN_DOMAIN']
missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    raise ValueError(f"缺少必要的环境变量: {', '.join(missing_vars)}\n请复制 .env.example 为 .env 并填写配置")

# 进度文件路径模板
PROGRESS_FILE_TEMPLATE = 'conversion_progress.yaml'

def get_progress_file():
    """获取进度文件路径"""
    return PROGRESS_FILE_TEMPLATE

def load_progress():
    """加载进度信息"""
    progress_file = get_progress_file()
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    return {}

def save_progress(progress):
    """保存进度信息"""
    progress_file = get_progress_file()
    with open(progress_file, 'w', encoding='utf-8') as f:
        yaml.dump(progress, f)

# 创建S3客户端
s3_client = boto3.client(
    's3',
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    config=Config(signature_version='s3v4')
)

def convert_image(image_path):
    """将图片转换为指定格式"""
    try:
        # 跳过远程图片
        if image_path.startswith(('http://', 'https://')):
            logging.info(f"跳过远程图片: {image_path}")
            return None, None

        img = Image.open(image_path)
        original_format = img.format.lower() if img.format else 'jpeg'
        
        # 转换为RGB模式（如果是RGBA，保持RGBA）
        if img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGB')
        
        # 创建一个字节流对象
        img_byte_arr = io.BytesIO()
        
        # 根据配置的格式保存图片
        if IMAGE_FORMAT == 'webp':
            img.save(img_byte_arr, format='WEBP', quality=80)
            img_byte_arr.seek(0)
            return img_byte_arr, 'webp'
        else:  # 保留原格式
            img.save(img_byte_arr, format=img.format, quality=80)
            img_byte_arr.seek(0)
            return img_byte_arr, original_format
            
    except Exception as e:
        logging.error(f"转换图片失败: {image_path}, 错误: {str(e)}")
        return None, None

def get_md5(content):
    """获取内容的MD5值"""
    if isinstance(content, io.BytesIO):
        return hashlib.md5(content.getvalue()).hexdigest()
    return hashlib.md5(content).hexdigest()

def upload_to_s3(img_data, relative_path='', img_format='webp'):
    """上传图片到S3"""
    try:
        md5_name = get_md5(img_data) + f'.{img_format}'
        # 使用文档的相对路径作为S3路径的一部分
        s3_path = f'{IMAGE_PATH_PREFIX}/{relative_path}/{md5_name}'.replace('//', '/')
        
        # 设置正确的Content-Type
        content_type = f'image/{img_format}'
        
        img_data.seek(0)
        s3_client.upload_fileobj(
            img_data,
            S3_BUCKET,
            s3_path,
            ExtraArgs={'ContentType': content_type}
        )
        
        return f'{CDN_DOMAIN}/{s3_path}'
    except Exception as e:
        logging.error(f"上传到S3失败: {str(e)}")
        return None

def process_markdown_file(file_path, progress, original_images_to_delete=None):
    """处理单个Markdown文件"""
    # 获取相对于根目录的路径作为文件键
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_key = os.path.relpath(file_path, base_dir)
    
    # 检查是否已处理
    if file_key in progress:
        logging.info(f"跳过已处理的文件: {file_key}")
        return True

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        original_content = content

        # 查找Markdown中的图片链接和HTML图片标签
        patterns = [
            r'!\[.*?\]\((.*?)\)',  # Markdown格式
            r'<img[^>]*?src=["\'](.*?)["\'][^>]*>'  # HTML格式
        ]
        modified = False
        success = True

        for pattern in patterns:
            matches = re.finditer(pattern, content)
            for match in matches:
                img_path = match.group(1)
                if img_path.startswith(CDN_DOMAIN):  # 跳过已经处理过的图片
                    continue
                
                # 如果是远程图片，跳过处理
                if img_path.startswith(('http://', 'https://')):
                    logging.info(f"跳过远程图片: {img_path}")
                    continue

                # 获取文档相对于根目录的路径作为S3子文件夹
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                relative_path = os.path.dirname(os.path.relpath(file_path, base_dir))
                
                # 转换为绝对路径
                absolute_img_path = os.path.abspath(os.path.join(os.path.dirname(file_path), img_path))
                
                # 转换图片
                img_data, img_format = convert_image(absolute_img_path)
                
                if img_data and img_format:
                    # 上传到S3
                    s3_url = upload_to_s3(img_data, relative_path, img_format)
                    if s3_url:
                        # 替换原文件中的图片链接
                        content = content.replace(match.group(0), match.group(0).replace(img_path, s3_url))
                        modified = True
                        logging.info(f"已处理图片: {img_path} -> {s3_url}")
                        
                        # 如果需要删除原图，添加到待删除列表
                        if DELETE_ORIGINAL_IMAGES and original_images_to_delete is not None:
                            original_images_to_delete.append(absolute_img_path)
                    else:
                        success = False
                        break
                else:
                    success = False
                    break

        # 只有在所有操作都成功时才写入文件
        if modified and success:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            progress[file_key] = {
                'processed_time': time.time(),
                'status': 'success'
            }
            save_progress(progress)
            logging.info(f"已处理文件: {file_key}")
            return True
        elif not success:
            logging.warning(f"处理失败，保持原文件不变: {file_key}")
            return False
        else:
            progress[file_key] = {
                'processed_time': time.time(),
                'status': 'no_changes_needed'
            }
            save_progress(progress)
            logging.info(f"文件无需修改: {file_key}")
            return True

    except Exception as e:
        logging.error(f"处理文件失败: {file_path}, 错误: {str(e)}")
        return False

def get_markdown_files(directory, exclude_dirs=None):
    """获取目录下所有的Markdown文件，排除指定目录"""
    if exclude_dirs is None:
        exclude_dirs = []
    
    # 将排除目录转换为绝对路径
    exclude_dirs_abs = [os.path.abspath(d) for d in exclude_dirs]
    
    markdown_files = []
    for root, dirs, files in os.walk(directory):
        # 检查当前目录是否在排除列表中
        if any(os.path.abspath(root).startswith(exclude_dir) for exclude_dir in exclude_dirs_abs):
            continue
            
        for file in files:
            if file.endswith('.md'):
                markdown_files.append(os.path.join(root, file))
    return markdown_files

def process_directory(directory, use_existing_progress=True, exclude_dirs=None):
    """处理目录下的所有Markdown文件"""
    progress_file = get_progress_file()
    
    # 如果选择不使用现有进度，则备份并重置进度文件
    if not use_existing_progress and os.path.exists(progress_file):
        backup_file = f"{progress_file}.bak.{int(time.time())}"
        shutil.copy2(progress_file, backup_file)
        logging.info(f"已备份进度文件到: {backup_file}")
        progress = {}
    else:
        progress = load_progress()
    
    markdown_files = get_markdown_files(directory, exclude_dirs)
    original_images_to_delete = [] if DELETE_ORIGINAL_IMAGES else None
    
    with tqdm(total=len(markdown_files), desc=f"处理Markdown文档") as pbar:
        for file_path in markdown_files:
            process_markdown_file(file_path, progress, original_images_to_delete)
            pbar.update(1)
    
    # 如果需要删除原图
    if DELETE_ORIGINAL_IMAGES and original_images_to_delete:
        logging.info(f"开始删除原始图片，共 {len(original_images_to_delete)} 张...")
        for img_path in original_images_to_delete:
            try:
                if os.path.exists(img_path):
                    os.remove(img_path)
                    logging.info(f"已删除原始图片: {img_path}")
            except Exception as e:
                logging.error(f"删除原始图片失败: {img_path}, 错误: {str(e)}")

def main():
    # 询问是否从之前的记录开始
    use_existing_progress = input("是否从之前的记录开始？(y/n): ").lower() == 'y'
    
    # 获取根目录（upload_images的上一级目录）
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # 排除upload_images目录本身
    upload_images_dir = os.path.dirname(os.path.abspath(__file__))
    exclude_dirs = [upload_images_dir]
    
    logging.info(f"开始处理根目录下的Markdown文档: {base_dir}")
    logging.info(f"排除目录: {exclude_dirs}")
    logging.info(f"图片格式设置: {IMAGE_FORMAT}")
    
    process_directory(base_dir, use_existing_progress, exclude_dirs)
    
    logging.info("完成处理所有Markdown文档")

if __name__ == '__main__':
    main()
