import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import hashlib
import time
import json
import logging
from typing import Dict, List, Optional, Any
from config import Config
from database import Chat, Message

class TimeoutSession(requests.Session):
    def request(self, *args, **kwargs):
        # Force a strict 15-second timeout on all requests
        kwargs.setdefault('timeout', 15)
        return super().request(*args, **kwargs)

def create_ggsel_session():
    """Create a persistent, self-healing session with strict timeouts."""
    session = TimeoutSession()

    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Connection': 'keep-alive'
    })

    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
    )
    
    adapter = requests.adapters.HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    return session

class GGSelAPI:
    def __init__(self, config):
        self.config = config
        self.base_url = config.ggsel_base_url
        self.token: Optional[str] = None
        self.session = create_ggsel_session()

    def _generate_sign(self, timestamp: str) -> str:
        """Генерация подписи"""
        data = f"{self.config.ggsel_api_key}{timestamp}"
        return hashlib.sha256(data.encode()).hexdigest()

    def login(self) -> bool:
        """Авторизация"""
        current_time = time.time()

        # --- ANTI-SPAM COOLDOWN ---
        if hasattr(self, '_last_auth_error_time') and self._last_auth_error_time:
            if current_time - self._last_auth_error_time < 60:
                return False

        timestamp = str(int(time.time()))
        sign = self._generate_sign(timestamp)
        payload = {"seller_id": self.config.ggsel_seller_id, "timestamp": timestamp, "sign": sign}

        try:
            response = self.session.post(f"{self.base_url}/apilogin", json=payload, timeout=15)

            if response.status_code == 200:
                data = response.json()
                if isinstance(data, dict) and 'token' in data:
                    self.token = data['token']
                    self._last_auth_error_time = None
                    return True
                    
            # Explicit check just in case the retry adapter doesn't catch the 503 first
            elif response.status_code in [502, 503, 504]:
                logging.error(f"GGSel Server Error ({response.status_code}). WAF block likely. Enforcing 300s cooldown.")
                self.session.close()
                self.session = create_ggsel_session()
                # Offset the timestamp by 240s so the 60s cooldown loop evaluates to 300s (5 mins) total
                self._last_auth_error_time = current_time + 240 
                return False

            return False

        except Exception as e:
            if "RemoteDisconnected" not in str(e):
                logging.error(f"Ошибка авторизации (GGSel API is down): {e}")

            # Destroy corrupted session pool and rebuild
            self.session.close()
            self.session = create_ggsel_session()

            # If GGSel blocked the connection or timed out, enforce the 5-minute cooldown
            if "Max retries" in str(e) or "Timeout" in str(e) or "503" in str(e):
                logging.warning("Applying 300-second firewall cooldown to prevent permanent IP ban.")
                self._last_auth_error_time = current_time + 240
            else:
                self._last_auth_error_time = current_time
                
            return False

    def get_chats(self, filter_new: Optional[int] = None, email: Optional[str] = None, 
                  id_ds: Optional[str] = None, pagesize: int = 100, page: int = 1) -> Optional[Dict[str, Any]]:
        """Получение чатов"""
        if not self.token and not self.login(): 
            return None
            
        params = {'token': self.token, 'pagesize': pagesize, 'page': page}
        if filter_new is not None: params['filter_new'] = filter_new
        if email: params['email'] = email
        if id_ds: params['id_ds'] = id_ds

        try:
            response = self.session.get(f"{self.base_url}/debates/v2/chats", params=params, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            if self.login(): 
                try:
                    params['token'] = self.token
                    response = self.session.get(f"{self.base_url}/debates/v2/chats", params=params, timeout=15)
                    response.raise_for_status()
                    return response.json()
                except: pass
            return None

    def get_chat_messages(self, chat_id: int) -> Optional[List[Dict[str, Any]]]:
        """Получение сообщений чата"""
        if not self.token and not self.login(): 
            return None
            
        params = {'token': self.token, 'id_i': chat_id}
        
        try:
            response = self.session.get(f"{self.base_url}/debates/v2", params=params, timeout=15)
            response.raise_for_status()
        except requests.RequestException:
            if self.login(): 
                try:
                    params['token'] = self.token
                    response = self.session.get(f"{self.base_url}/debates/v2", params=params, timeout=15)
                    response.raise_for_status()
                except: return None
            else: return None
            
        try:
            data = response.json()
            if isinstance(data, list): return data
            elif isinstance(data, dict) and 'messages' in data: return data['messages']
            return []
        except: 
            return None

    def send_message(self, chat_id: int, message: str) -> bool:
        """Отправка сообщения"""
        if not self.token and not self.login(): 
            return False
            
        if len(message) > 4000: message = message[:4000]
        
        url = f"{self.base_url}/debates/v2"
        params = {'token': self.token, 'id_i': chat_id}
        
        safe_message = message if message else "💬" 
        payload = {'message': safe_message, 'text': safe_message}
            
        try:
            response = self.session.post(url, params=params, json=payload, timeout=30)
            if response.status_code == 200:
                try:
                    return response.json().get('retval') == 0
                except json.JSONDecodeError:
                    return True
            return False
            
        except requests.exceptions.ReadTimeout:
            logging.warning(f"Timeout on chat {chat_id}: Message likely sent, but GGSel is slow.")
            return True
            
        except Exception as e:
            logging.error(f"Ошибка отправки: {e}")
            return False
    
    def get_last_sales(self, top: int = 10) -> Optional[Dict[str, Any]]:
        """Получение продаж"""
        if not self.token and not self.login():
            return None
        
        url = f"{self.base_url}/seller-last-sales"
        params = {'token': self.token, 'top': top}
        headers = {'Accept': 'application/json', 'locale': 'ru'}
        
        try:
            response = self.session.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            if self.login():
                try:
                    params['token'] = self.token
                    response = self.session.get(url, params=params, headers=headers)
                    response.raise_for_status()
                    return response.json()
                except:
                    pass
            return None
    
    def get_purchase_info(self, invoice_id: int) -> Optional[Dict[str, Any]]:
        """Получение информации о покупке"""
        if not self.token and not self.login():
            return None
        
        url = f"{self.base_url}/purchase/info/{invoice_id}?token={self.token}"
        headers = {'Accept': 'application/json'}
        
        try:
            response = self.session.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                if data.get('retval') == 0:
                    return data
            return None
        except requests.RequestException:
            if self.login():
                try:
                    url = f"{self.base_url}/purchase/info/{invoice_id}?token={self.token}"
                    response = self.session.get(url, headers=headers, timeout=30)
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('retval') == 0:
                            return data
                except:
                    pass
            return None
    
    def get_chats_by_email(self, email: str, pagesize: int = 100, page: int = 1) -> Optional[Dict[str, Any]]:
        """Получение чатов по email"""
        if not self.token and not self.login():
            return None
        
        params = {'token': self.token, 'email': email, 'pagesize': pagesize, 'page': page}
        
        try:
            response = self.session.get(f"{self.base_url}/debates/v2/chats", params=params)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            if self.login():
                try:
                    params['token'] = self.token
                    response = self.session.get(f"{self.base_url}/debates/v2/chats", params=params)
                    response.raise_for_status()
                    return response.json()
                except:
                    pass
            return None
    
    def parse_chats_response(self, response_data: Dict[str, Any]) -> List[Chat]:
        """Парсинг чатов"""
        chats = []
        if 'items' in response_data:
            for item in response_data['items']:
                id_i = item.get('id_i')
                if id_i is None:
                    continue
                
                chat = Chat(
                    id_i=id_i,
                    email=item.get('email') or None,
                    product=item.get('product', 0),
                    last_message=item.get('last_message', ''),
                    cnt_msg=item.get('cnt_msg', 0),
                    cnt_new=item.get('cnt_new', 0)
                )
                chats.append(chat)
        return chats
    
    def get_reviews(self, count: int = 20, review_type: str = "all", page: int = 1, product_id: int = None) -> Optional[Dict[str, Any]]:
        """Получение отзывов"""
        if not self.token and not self.login():
            return None
        
        url = "https://seller.ggsel.com/api_sellers/api/reviews"
        params = {
            'token': self.token,
            'type': review_type,
            'page': page,
            'count': count
        }
        if product_id:
            params['product_id'] = product_id
            
        headers = {'Accept': 'application/json', 'locale': 'ru-RU'}
        
        try:
            response = self.session.get(url, params=params, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                if data.get('retval') == 0:
                    return data
            return None
        except requests.RequestException as e:
            logging.debug(f"Ошибка получения отзывов: {e}")
            if self.login():
                try:
                    params['token'] = self.token
                    response = self.session.get(url, params=params, headers=headers, timeout=30)
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('retval') == 0:
                            return data
                except:
                    pass
            return None

    def get_review_by_invoice(self, invoice_id: int) -> Optional[Dict[str, Any]]:
        """Поиск отзыва по invoice_id"""
        for page in range(1, 20): 
            data = self.get_reviews(count=50, page=page)
            if not data:
                break
            
            reviews = data.get('reviews', [])
            if not reviews:
                break
            
            for review in reviews:
                if review.get('invoice_id') == invoice_id:
                    return review
        
        return None
        
    def get_real_product_name(self, item_id: int) -> Optional[str]:
        """Fetches product info via the official GGSel API to determine the real name."""
        if not item_id:
            return None
            
        url = f"https://seller.ggsel.com/api_sellers/api/products/{item_id}/data"
        params = {"token": self.token}
        
        try:
            response = self.session.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                product_data = data.get('product')
                
                if product_data and 'name' in product_data:
                    clean_name = str(product_data['name']).strip()
                    return clean_name
                else:
                    logging.warning(f"Product 'name' key missing in API response for {item_id}")
            else:
                logging.error(f"Official API fetch failed with HTTP {response.status_code}")
        except Exception as e:
            logging.error(f"API fetch exception for {item_id}: {e}")
            
        return None