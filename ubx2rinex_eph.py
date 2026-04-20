import tkinter as tk
from tkinter import filedialog, messagebox
import tkinter.scrolledtext as scrolledtext
import subprocess
import os
import threading
import datetime
import requests

# 리다이렉트 시에도 아이디/패스워드를 유지하게 해주는 특수 세션 클래스
class EarthDataSession(requests.Session):
    def __init__(self, username, password):
        super().__init__()
        self.auth = (username, password)

    def rebuild_auth(self, prepared_request, response):
        # 도메인이 바뀌어도 Authorization 헤더를 날리지 않도록 오버라이딩
        pass

# ==========================================
# 1. 사용자 환경 세팅 (경로 및 NASA 계정 정보)
# ==========================================
CONVBIN_PATH = "/home/rideflux/work/python_IE/convbin"  # RTKLIB convbin 실행 파일 경로

# NASA Earthdata (CDDIS) 계정 정보 입력
NASA_ID = "topika"          # 본인의 아이디로 변경하세요
NASA_PW = "05skdiSKDI!!"    # 본인의 패스워드로 변경하세요

class GNSSProcessorUI:
    def __init__(self, root):
        self.root = root
        self.root.title("GNSS Data Processor (UBX to RINEX & SP3)")
        self.root.geometry("600x400")

        self.ubx_file_path = tk.StringVar()
        self.setup_ui()

    def setup_ui(self):
        # 1. 파일 선택 영역
        frame_file = tk.Frame(self.root)
        frame_file.pack(pady=20, padx=10, fill='x')
        
        tk.Label(frame_file, text="UBX 파일:").pack(side='left')
        tk.Entry(frame_file, textvariable=self.ubx_file_path, width=50).pack(side='left', padx=5)
        tk.Button(frame_file, text="찾아보기", command=self.browse_file).pack(side='left')

        # 2. 실행 버튼
        self.btn_run = tk.Button(self.root, text="변환 및 궤도 파일 다운로드", command=self.start_processing, bg="#4CAF50", fg="white", font=("Arial", 10, "bold"))
        self.btn_run.pack(pady=10)

        # 3. 로그 출력 창
        self.log_text = scrolledtext.ScrolledText(self.root, width=75, height=15, state='disabled')
        self.log_text.pack(padx=10, pady=5)

    def log(self, message):
        """로그 창에 메시지를 출력합니다."""
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')
        self.root.update()

    def browse_file(self):
        filename = filedialog.askopenfilename(filetypes=[("UBX files", "*.ubx"), ("All files", "*.*")])
        if filename:
            self.ubx_file_path.set(filename)

    def start_processing(self):
        ubx_file = self.ubx_file_path.get()

        if not os.path.exists(ubx_file):
            messagebox.showerror("오류", "유효한 UBX 파일을 선택하세요.")
            return

        if not os.path.exists(CONVBIN_PATH):
            messagebox.showerror("오류", f"RTKLIB convbin 경로를 찾을 수 없습니다:\n{CONVBIN_PATH}")
            return

        self.btn_run.config(state='disabled')
        self.log("--- 작업 시작 ---")
        
        # UI가 멈추지 않도록 스레드에서 실행
        threading.Thread(target=self.process_workflow, args=(ubx_file,), daemon=True).start()

    def process_workflow(self, ubx_file):
        try:
            # 기본 경로 및 파일명 추출
            original_dir = os.path.dirname(ubx_file)
            base_name = os.path.splitext(os.path.basename(ubx_file))[0]
            
            # UBX 파일명과 동일한 폴더 생성
            target_dir = os.path.join(original_dir, base_name)
            if not os.path.exists(target_dir):
                os.makedirs(target_dir)
                self.log(f"-> 폴더 생성됨: {target_dir}")

            rinex_o_file = os.path.join(target_dir, base_name + ".obs")
            
            # Step 1: UBX to RINEX (convbin)
            self.log("\n[Step 1] UBX 파일을 RINEX(.o)로 변환 중...")
            cmd_convbin = [CONVBIN_PATH, "-d", target_dir, "-o", f"{base_name}.obs", ubx_file]
            result = subprocess.run(cmd_convbin, capture_output=True, text=True)
            
            if not os.path.exists(rinex_o_file):
                self.log(f"변환 실패: {result.stderr}")
                raise Exception("RINEX 파일 생성에 실패했습니다.")
            self.log(f"-> 완료: {base_name}.obs 생성됨")

            # Step 2: RINEX 파일에서 시간 정보 추출
            obs_date = self.extract_time_from_rinex(rinex_o_file)
            if not obs_date:
                raise Exception("RINEX 파일에서 관측 시작 시간을 찾을 수 없습니다.")
            self.log(f"\n[Step 2] 추출된 관측 날짜: {obs_date.strftime('%Y-%m-%d')}")

            # Step 3: NASA CDDIS에서 SP3 궤도 파일 다운로드
            self.log(f"\n[Step 3] NASA CDDIS 정밀 궤도 파일 다운로드 중...")
            self.download_nasa_orbit(obs_date, target_dir)

            self.log("\n--- 모든 작업이 성공적으로 완료되었습니다 ---")

        except Exception as e:
            self.log(f"\n[에러 발생] {str(e)}")
        finally:
            self.btn_run.config(state='normal')

    def extract_time_from_rinex(self, rinex_path):
        """RINEX 파일 헤더에서 'TIME OF FIRST OBS'를 찾아 datetime으로 반환합니다."""
        try:
            with open(rinex_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if "TIME OF FIRST OBS" in line:
                        parts = line.split()
                        year = int(parts[0])
                        # RINEX 2.x 에서는 두 자리 연도가 올 수 있으므로 보정 (필요시)
                        if year < 100:
                            year += 2000 if year < 80 else 1900
                        month = int(parts[1])
                        day = int(parts[2])
                        return datetime.datetime(year, month, day)
                    if "END OF HEADER" in line:
                        break
        except Exception as e:
            self.log(f"시간 추출 오류: {str(e)}")
        return None

    def download_nasa_orbit(self, dt, target_dir):
            """관측 시간을 기준으로 GPS Week를 계산하고 NASA CDDIS에서 궤도 파일을 다운로드합니다."""
            gps_epoch = datetime.datetime(1980, 1, 6)
            days_since = (dt - gps_epoch).days
            gps_week = days_since // 7
            
            # 1. 새 IGS 네이밍 규칙(Long Filename)을 위해 연도와 DOY(Day of Year) 계산
            year = dt.year
            doy = dt.timetuple().tm_yday
            
            # 2. 시도할 궤도 파일 리스트 (Final 먼저 시도, 없으면 Rapid 시도)
            # 규격 예시: IGS0OPSFIN_YYYYDDD0000_01D_15M_ORB.SP3.gz
            filenames_to_try = [
                f"IGS0OPSFIN_{year}{doy:03d}0000_01D_15M_ORB.SP3.gz", # 1순위: Final (최종 정밀 궤도)
                f"IGS0OPSRAP_{year}{doy:03d}0000_01D_15M_ORB.SP3.gz"  # 2순위: Rapid (신속 궤도 - Final 산출 전일 경우)
            ]

            # 커스텀 세션 사용 (EarthDataSession)
            session = EarthDataSession(NASA_ID, NASA_PW)

            for file_name in filenames_to_try:
                download_url = f"https://cddis.nasa.gov/archive/gnss/products/{gps_week}/{file_name}"
                save_path = os.path.join(target_dir, file_name)

                self.log(f"-> 다운로드 시도: {file_name}")

                try:
                    # 서버 인증 및 다운로드 요청
                    response = session.get(download_url, allow_redirects=True, stream=True, timeout=30)
                    
                    if response.status_code == 200:
                        # 다운로드 성공 시 HTML(로그인 에러) 여부 확인
                        if 'text/html' in response.headers.get('Content-Type', ''):
                            self.log("-> [실패] 다운로드된 파일이 HTML입니다. Earthdata 앱 승인 상태를 확인하세요.")
                            return

                        # 정상적인 파일 저장
                        with open(save_path, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                        self.log(f"-> 궤도 파일 저장 완료: {save_path}")
                        return  # 성공했으므로 함수 종료
                    
                    elif response.status_code == 404:
                        self.log("-> [404 Not Found] 해당 파일을 찾을 수 없습니다. (다음 파일 탐색 시도)")
                        continue  # 404면 다음 파일명(Rapid)으로 넘어감
                        
                    else:
                        self.log(f"-> 다운로드 에러 (상태 코드: {response.status_code})")
                        return
                        
                except requests.exceptions.RequestException as e:
                    self.log(f"-> 네트워크 통신 오류: {str(e)}")
                    return
                    
            # 리스트에 있는 모든 파일을 시도했는데도 못 찾은 경우
            self.log("-> [최종 실패] 서버에서 해당 날짜의 정밀(Final) 및 신속(Rapid) 궤도 파일을 모두 찾지 못했습니다.")

if __name__ == "__main__":
    root = tk.Tk()
    app = GNSSProcessorUI(root)
    root.mainloop()