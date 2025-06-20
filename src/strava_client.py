import os
import sys
from datetime import datetime, timezone

# 获取项目根目录路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 动态添加Python模块搜索路径
user_site_packages = os.path.expanduser("~/.local/lib/python3.10/site-packages")
system_dist_packages = "/usr/lib/python3/dist-packages"

# 将路径添加到sys.path开头，优先级更高
if user_site_packages not in sys.path:
    sys.path.insert(0, user_site_packages)
if system_dist_packages not in sys.path:
    sys.path.insert(0, system_dist_packages)
    
import time
import logging
import requests
import json
from typing import List, Dict, Optional, Tuple

from config_manager import ConfigManager
from file_utils import FileUtils
from ui_utils import UIUtils
from database_manager import ActivityMetadata

logger = logging.getLogger(__name__)

class StravaClient:
    """扩展的Strava客户端，支持双向同步功能"""
    
    def __init__(self, config_manager: ConfigManager, debug: bool = False):
        self.config_manager = config_manager
        self.debug = debug
        self.base_url = "https://www.strava.com/api/v3"
    
    def debug_print(self, message: str) -> None:
        """只在调试模式下打印信息"""
        if self.debug:
            print(f"[StravaClient] {message}")
    
    def is_configured(self) -> bool:
        """检查Strava是否已配置"""
        config = self.config_manager.get_platform_config("strava")
        return bool(config.get("client_id") and config.get("client_secret") and config.get("refresh_token"))
    
    def _refresh_access_token(self) -> bool:
        """刷新访问令牌"""
        config = self.config_manager.get_platform_config("strava")
        
        refresh_data = {
            'client_id': config.get("client_id"),
            'client_secret': config.get("client_secret"),
            'refresh_token': config.get("refresh_token"),
            'grant_type': 'refresh_token'
        }
        
        try:
            print("刷新Strava访问令牌...")
            response = requests.post('https://www.strava.com/oauth/token', data=refresh_data)
            print(f"Token刷新响应状态码: {response.status_code}")
            
            if response.status_code == 200:
                token_data = response.json()
                
                # 更新配置中的访问令牌
                config["access_token"] = token_data['access_token']
                config["refresh_token"] = token_data['refresh_token']
                self.config_manager.save_platform_config("strava", config)
                
                print("Strava访问令牌刷新成功")
                return True
            else:
                print(f"Token刷新失败: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"刷新Strava访问令牌失败: {e}")
            return False
    
    def _get_headers(self) -> Dict[str, str]:
        """获取API请求头"""
        config = self.config_manager.get_platform_config("strava")
        access_token = config.get("access_token")
        
        if not access_token:
            if not self._refresh_access_token():
                raise Exception("无法获取有效的访问令牌")
            access_token = config.get("access_token")
        
        return {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
    
    def get_activities(self, limit: int = 30, page: int = 1) -> List[Dict]:
        """获取活动列表"""
        try:
            headers = self._get_headers()
            params = {
                'per_page': min(limit, 200),  # Strava限制每页最多200
                'page': page
            }
            
            print(f"获取Strava活动列表，限制: {limit}")
            response = requests.get(f"{self.base_url}/athlete/activities", 
                                  headers=headers, params=params)
            print(f"活动列表响应状态码: {response.status_code}")
            
            if response.status_code == 200:
                activities = response.json()
                print(f"成功获取{len(activities)}个活动")
                return activities
            elif response.status_code == 401:
                # Token可能过期，尝试刷新
                if self._refresh_access_token():
                    headers = self._get_headers()
                    response = requests.get(f"{self.base_url}/athlete/activities", 
                                          headers=headers, params=params)
                    if response.status_code == 200:
                        activities = response.json()
                        print(f"重试后成功获取{len(activities)}个活动")
                        return activities
                
                raise Exception(f"认证失败: {response.text}")
            else:
                raise Exception(f"获取活动失败: {response.status_code} - {response.text}")
                
        except Exception as e:
            logger.error(f"获取Strava活动失败: {e}")
            return []
    
    def get_activities_in_batches(self, total_limit: int = 50, 
                                after: Optional[datetime] = None,
                                before: Optional[datetime] = None) -> List[Dict]:
        """分批获取活动"""
        all_activities = []
        page = 1
        per_page = min(30, total_limit)
        
        while len(all_activities) < total_limit:
            remaining = total_limit - len(all_activities)
            current_limit = min(per_page, remaining)
            
            print(f"获取第{page}页活动，每页{current_limit}个")
            activities = self.get_activities(limit=current_limit, page=page)
            
            if not activities:
                break
            
            # 时间过滤
            filtered_activities = []
            for activity in activities:
                activity_time = datetime.fromisoformat(activity['start_date'].replace('Z', '+00:00'))
                
                if after:
                    # 确保after有时区信息
                    after_tz = after
                    if after_tz.tzinfo is None:
                        after_tz = after_tz.replace(tzinfo=timezone.utc)
                    if activity_time < after_tz:
                        continue
                        
                if before:
                    # 确保before有时区信息
                    before_tz = before
                    if before_tz.tzinfo is None:
                        before_tz = before_tz.replace(tzinfo=timezone.utc)
                    if activity_time > before_tz:
                        continue
                        
                filtered_activities.append(activity)
            
            all_activities.extend(filtered_activities)
            
            # 如果这一页的活动数量少于请求数量，说明没有更多了
            if len(activities) < current_limit:
                break
                
            page += 1
        
        print(f"总共获取{len(all_activities)}个活动")
        return all_activities[:total_limit]
    
    def convert_to_activity_metadata(self, strava_activity: Dict) -> ActivityMetadata:
        """将Strava活动数据转换为ActivityMetadata"""
        return ActivityMetadata(
            name=strava_activity.get("name", "未命名活动"),
            sport_type=strava_activity.get("sport_type", "unknown"),
            start_time=strava_activity.get("start_date", ""),
            distance=float(strava_activity.get("distance", 0)),
            duration=int(strava_activity.get("elapsed_time", 0)),
            elevation_gain=float(strava_activity.get("total_elevation_gain", 0))
        )
    
    def get_activity_details(self, activity_id: str) -> Dict:
        """获取活动详细信息"""
        access_token = self._get_headers()['Authorization'].split(' ')[1]
        self.debug_print(f"获取活动{activity_id}的详细信息")
        
        url = f"{self.base_url}/activities/{activity_id}"
        headers = {"Authorization": f"Bearer {access_token}"}
        
        try:
            response = requests.get(url, headers=headers)
            self.debug_print(f"活动详情响应状态码: {response.status_code}")
            
            if response.status_code == 200:
                return response.json()
            else:
                self.debug_print(f"获取活动详情失败: {response.text}")
                raise ValueError(f"无法获取活动{activity_id}的详情")
                
        except Exception as e:
            logger.error(f"获取Strava活动详情失败: {e}")
            raise
    
    def select_activity_from_api(self) -> Tuple[str, Optional[str]]:
        """从API获取活动并让用户选择，返回(activity_id, activity_name)"""
        # 检查Strava配置
        if not self.is_configured():
            print("\n检测到默认的Strava API配置")
            print("请按照以下步骤获取Strava API凭据:")
            print("1. 访问 https://www.strava.com/settings/api")
            print("2. 创建应用程序获取 Client ID 和 Client Secret")
            print("3. 使用OAuth流程获取 Refresh Token")
            print("4. 更新 .app_config.json 文件中的strava配置")
            
            use_manual = UIUtils.ask_manual_token("Strava活动ID")
            
            if use_manual:
                return UIUtils.ask_activity_id(), None
            else:
                raise ValueError("请先配置Strava API凭据")
        
        try:
            # 获取活动列表
            activities = self.get_activities()
            
            if not activities:
                print("未找到任何活动")
                return UIUtils.ask_activity_id(), None
            
            # 让用户选择活动
            return UIUtils.select_activity_from_list(activities)
                
        except Exception as e:
            logger.error(f"从API获取活动失败: {e}")
            print(f"从API获取活动失败: {e}")
            print("将使用手动输入方式...")
            return UIUtils.ask_activity_id(), None
    
    def download_file(self, activity_id: str, activity_name: Optional[str] = None) -> Optional[str]:
        """下载活动文件"""
        # 统一使用export_original下载fit文件，不区分运动类型
        url = f"https://www.strava.com/activities/{activity_id}/export_original"
        
        self.debug_print(f"\n开始下载活动 {activity_id} 的原始文件...")
        self.debug_print(f"活动名称: {activity_name}")
        self.debug_print(f"下载URL: {url}")
        
        # 检查是否已存在相同活动ID的文件
        existing_file = FileUtils.check_existing_activity_file(activity_id, activity_name)
        if existing_file:
            print(f"发现已存在的活动文件: {os.path.basename(existing_file)}")
            if UIUtils.confirm_use_existing_file(os.path.basename(existing_file)):
                print("跳过下载，使用已存在的文件")
                return existing_file
            else:
                print("继续下载新文件...")
        
        # 直接使用Cookie认证下载
        return self._download_with_cookie(url, activity_id, activity_name)
    
    def download_activity_file(self, activity_id: str, save_path: str) -> bool:
        """下载活动文件"""
        try:
            self.debug_print(f"下载Strava活动文件: {activity_id}")
            
            # 使用现有的下载逻辑
            downloaded_file = self.download_file(activity_id)
            
            if downloaded_file and os.path.exists(downloaded_file):
                # 如果下载成功，移动文件到指定路径
                import shutil
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                shutil.move(downloaded_file, save_path)
                self.debug_print(f"文件已下载到: {save_path}")
                return True
            else:
                self.debug_print("文件下载失败")
                return False
                
        except Exception as e:
            self.debug_print(f"下载活动文件失败: {e}")
            logger.error(f"下载Strava活动文件失败: {e}")
            return False
    
    def _download_with_cookie(self, url: str, activity_id: str, activity_name: Optional[str] = None) -> Optional[str]:
        """使用Cookie进行认证下载"""
        config = self.config_manager.get_platform_config("strava")
        
        # 首先尝试使用保存的Cookie
        saved_cookie = config.get("cookie", "")
        
        if saved_cookie:
            self.debug_print("使用已保存的Cookie进行下载...")
            success, file_path = self._try_download_with_cookie(url, activity_id, saved_cookie, activity_name)
            if success:
                return file_path
            else:
                self.debug_print("保存的Cookie可能已过期，需要更新Cookie")
        
        # 如果没有保存的Cookie或Cookie已过期，提示用户输入新的Cookie
        print("\n要获取Strava Cookie，请按以下步骤操作：")
        print("1. 在浏览器中打开 https://www.strava.com 并登录")
        print("2. 按F12打开开发者工具")
        print("3. 转到 Network(网络) 标签")
        print("4. 刷新页面")
        print("5. 找到任意一个请求，在Request Headers中找到Cookie")
        print("6. 复制完整的Cookie值")
        
        cookie_value = UIUtils.ask_manual_token("Strava Cookie")
        
        if not cookie_value:
            print("未提供Cookie，无法下载文件")
            raise ValueError("Cookie为空，无法继续")
        
        # 尝试使用新Cookie下载
        success, file_path = self._try_download_with_cookie(url, activity_id, cookie_value, activity_name)
        
        if success:
            # 保存Cookie供下次使用
            config["cookie"] = cookie_value
            self.config_manager.save_platform_config("strava", config)
            return file_path
        else:
            print("Cookie无效或活动不可访问")
            raise ValueError("下载失败")
    
    def _try_download_with_cookie(self, url: str, activity_id: str, cookie: str, 
                                  activity_name: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """尝试使用Cookie下载文件"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Cookie': cookie.strip(),
                'Referer': f'https://www.strava.com/activities/{activity_id}'
            }
            
            self.debug_print(f"发送下载请求...")
            response = requests.get(url, headers=headers, timeout=30)
            
            self.debug_print(f"响应状态码: {response.status_code}")
            self.debug_print(f"Content-Type: {response.headers.get('content-type', 'Unknown')}")
            self.debug_print(f"Content-Length: {response.headers.get('content-length', 'Unknown')}")
            
            if response.status_code == 200:
                content_type = response.headers.get('content-type', '').lower()
                
                # 生成文件名
                if activity_name:
                    # 使用活动名生成文件名
                    clean_name = FileUtils.sanitize_filename(activity_name)
                    base_filename = f"{clean_name}_{activity_id}"
                else:
                    # 如果没有活动名，使用默认格式
                    base_filename = f"activity_{activity_id}"
                
                # 判断文件类型并保存
                download_path = self._save_downloaded_file(response, base_filename, content_type)
                
                if download_path:
                    return True, download_path
                else:
                    return False, None
                    
            else:
                self.debug_print(f"下载失败 (状态码: {response.status_code})")
                return False, None
                
        except Exception as e:
            self.debug_print(f"下载出错: {e}")
            return False, None
    
    def _save_downloaded_file(self, response: requests.Response, base_filename: str, content_type: str) -> Optional[str]:
        """保存下载的文件"""
        try:
            if 'application/octet-stream' in content_type or 'application/fit' in content_type:
                # FIT文件（二进制）
                filename = f"{base_filename}.fit"
                download_path = os.path.join(os.path.expanduser("~/Downloads"), filename)
                
                with open(download_path, 'wb') as f:
                    f.write(response.content)
                
                print(f"FIT文件已成功下载: {filename}")
                self.debug_print(f"文件大小: {len(response.content)} bytes")
                return download_path
                
            elif 'xml' in content_type or '<?xml' in response.text:
                # XML格式文件（TCX/GPX）
                content = response.text
                if 'TrainingCenterDatabase' in content:
                    filename = f"{base_filename}.tcx"
                elif 'gpx' in content.lower():
                    filename = f"{base_filename}.gpx"
                else:
                    filename = f"{base_filename}.xml"
                    
                download_path = os.path.join(os.path.expanduser("~/Downloads"), filename)
                
                with open(download_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                
                print(f"XML文件已成功下载: {filename}")
                self.debug_print(f"文件大小: {len(content)} characters")
                return download_path
            else:
                self.debug_print(f"未知的文件格式，Content-Type: {content_type}")
                if hasattr(response, 'text'):
                    preview = response.text[:200] if response.text else str(response.content[:200]) 
                    self.debug_print(f"响应内容开头: {preview}")
                return None
                
        except Exception as e:
            self.debug_print(f"文件保存失败: {e}")
            return None
    
    def get_activities_for_migration(self, batch_size: int = 10, 
                                    after: Optional[datetime] = None,
                                    before: Optional[datetime] = None) -> List[Dict]:
        """获取用于历史迁移的活动列表
        
        Args:
            batch_size: 每批处理的活动数量
            after: 开始时间（从这个时间之后开始获取）
            before: 结束时间（获取到这个时间为止）
        
        Returns:
            按时间顺序排列的活动列表（最老的在前）
        """
        print(f"获取历史迁移活动，批次大小: {batch_size}")
        if after:
            print(f"开始时间: {after}")
        if before:
            print(f"结束时间: {before}")
        
        all_activities = []
        page = 1
        per_page = 200  # Strava API最大每页200个
        
        # 获取足够多的活动以便筛选
        max_pages = 50  # 最多获取50页，避免无限循环
        
        while page <= max_pages:
            try:
                headers = self._get_headers()
                params = {
                    'per_page': per_page,
                    'page': page
                }
                
                # 如果有after参数，添加到API请求中
                if after:
                    # Strava API使用Unix时间戳
                    params['after'] = int(after.timestamp())
                
                if before:
                    params['before'] = int(before.timestamp())
                
                print(f"获取第{page}页活动...")
                response = requests.get(f"{self.base_url}/athlete/activities", 
                                      headers=headers, params=params)
                
                if response.status_code == 200:
                    activities = response.json()
                    print(f"第{page}页获取到{len(activities)}个活动")
                    
                    if not activities:
                        print("没有更多活动，停止获取")
                        break
                    
                    # 时间过滤（双重保险）
                    filtered_activities = []
                    for activity in activities:
                        activity_time = datetime.fromisoformat(activity['start_date'].replace('Z', '+00:00'))
                        
                        # 检查时间范围
                        if after:
                            # 确保after有时区信息
                            after_tz = after
                            if after_tz.tzinfo is None:
                                after_tz = after_tz.replace(tzinfo=timezone.utc)
                            if activity_time < after_tz:
                                continue
                                
                        if before:
                            # 确保before有时区信息
                            before_tz = before
                            if before_tz.tzinfo is None:
                                before_tz = before_tz.replace(tzinfo=timezone.utc)
                            if activity_time > before_tz:
                                continue
                                
                        filtered_activities.append(activity)
                    
                    all_activities.extend(filtered_activities)
                    print(f"过滤后添加{len(filtered_activities)}个活动，总计{len(all_activities)}个")
                    
                    # 如果已经获取足够的活动，停止
                    if len(all_activities) >= batch_size:
                        break
                    
                    # 如果这一页的活动数量少于请求数量，说明没有更多了
                    if len(activities) < per_page:
                        print("已获取所有可用活动")
                        break
                        
                    page += 1
                    
                elif response.status_code == 401:
                    # Token可能过期，尝试刷新
                    if self._refresh_access_token():
                        continue  # 重试当前页
                    else:
                        raise Exception("认证失败，无法刷新token")
                else:
                    raise Exception(f"获取活动失败: {response.status_code} - {response.text}")
                    
            except Exception as e:
                logger.error(f"获取第{page}页活动失败: {e}")
                break
        
        # 按时间排序（最老的在前）
        all_activities.sort(key=lambda x: x['start_date'])
        
        # 只返回需要的数量
        result = all_activities[:batch_size]
        print(f"最终返回{len(result)}个活动用于迁移")
        
        if result:
            first_activity_time = result[0]['start_date']
            last_activity_time = result[-1]['start_date']
            print(f"活动时间范围: {first_activity_time} 到 {last_activity_time}")
        
        return result 