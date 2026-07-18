# 🦾 LeLab 설치 및 실행 가이드 (Windows / Linux Installation Guide)

이 문서는 **Windows 및 Linux 환경**에서 **LeLab**(LeRobot의 공식 웹 그래픽 인터페이스)을 설치하고 실행하기 위한 가이드입니다. 강의 내용 및 개발 표준(CLAUDE.md)을 바탕으로 작성되었습니다.

---

## 📋 시스템 요구사항 (Prerequisites)

- **OS**: Windows 10 이상 또는 Linux (Ubuntu 22.04 이상 권장)
- **Python**: 3.12 이상 (권장)
- **Git**: 소스 코드 복제용
- **uv**: 빠르고 안정적인 패키지 및 가상환경 관리를 위해 강력히 권장합니다.

---

## ⚙️ 1단계: 개발 환경 구성

### 1. `uv` 설치 (미설치된 경우)
- **Windows (PowerShell)**:
  ```powershell
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
- **Linux (bash)**:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # 설치 후 새 터미널을 열거나 아래로 PATH를 갱신합니다
  source ~/.local/bin/env
  ```

### 2. 가상환경(Virtual Environment) 생성
프로젝트 루트 폴더(`leLab/`)로 이동하여 Python 3.12 기반의 가상환경을 생성합니다. (`uv`는 필요한 Python 버전을 자동으로 감지하여 다운로드합니다.) 이 명령어는 Windows/Linux 공통입니다.
```bash
uv venv --python 3.12
```

### 3. 가상환경 활성화 (Activate)
- **Windows PowerShell**:
  ```powershell
  .venv\Scripts\Activate.ps1
  ```
- **Windows 기본 CMD**:
  ```cmd
  .venv\Scripts\activate.bat
  ```
- **Linux (bash/zsh)**:
  ```bash
  source .venv/bin/activate
  ```

---

## 📥 2단계: 패키지 및 종속성 설치

개발/테스트 도구를 포함하여 **Editable 모드(-e)**로 패키지를 설치합니다. 이 명령어는 Windows/Linux 공통입니다:

```bash
# 개발 및 테스트 종속성을 함께 설치
uv pip install -e ".[dev,test]"
```

> [!NOTE]
> - Windows 환경에서는 카메라의 브라우저 `deviceId`와 실제 DirectShow 카메라 이름을 매핑하기 위해 `pygrabber` 패키지가 자동으로 함께 설치됩니다. (`pyproject.toml`에 `sys_platform == 'win32'` 조건이 걸려 있어 Linux에서는 설치되지 않으며, Linux는 V4L2 백엔드를 사용합니다.)
> - `lerobot` 핵심 라이브러리는 `pyproject.toml`에 명시된 Hugging Face 공식 리포지토리의 특정 커밋 버전을 참조하여 자동으로 빌드 및 설치됩니다.

---

## 🚀 3단계: LeLab 실행하기

**기본 실행 명령어는 `uv run`을 사용합니다.** `uv run`은 가상환경 활성화 없이도 프로젝트의 `.venv`를 자동으로 찾아 실행하고, 의존성이 바뀌었으면 실행 전에 자동으로 동기화까지 해주므로 가장 안전하고 간편합니다. 명령어는 Windows/Linux 공통입니다.

### A. 개발자 모드 — 기본 권장 (핫 리로드 및 실시간 코드 수정)
백엔드 FastAPI 서버(`:8000`, 자동 재실행 적용)와 프론트엔드 Vite 개발 서버(`:8080`)를 동시에 띄우며, 코드 수정 시 실시간으로 화면에 반영됩니다.
```bash
uv run lelab --dev
```

### B. 일반 모드 (서버 & 빌드된 프론트엔드 통합 실행)
Vite로 빌드된 정적 프론트엔드 파일과 FastAPI 백엔드가 포트 `8000`에서 동시에 실행되며, 브라우저 창이 자동으로 열립니다.
```bash
uv run lelab
```

> [!NOTE]
> - 옵션은 이중 하이픈입니다: `--dev` (○) / `-dev` (✕ — `unrecognized arguments` 오류 발생)
> - 가상환경을 이미 활성화한 상태라면 `uv run` 없이 `lelab --dev` / `lelab`으로 실행해도 동일합니다.
> - 개발자 모드는 프론트엔드 빌드를 위해 **Node.js LTS**(및 npm)가 필요합니다. Linux에서는 `sudo apt install nodejs npm` 또는 [nvm](https://github.com/nvm-sh/nvm)으로 설치하세요. 백엔드 최초 기동은 `lerobot`(torch 포함) 로딩 때문에 20~30초가량 걸릴 수 있습니다.
> - 이전 실행이 포트(`8000`/`8080`)를 잡고 있어 시작에 실패하면 `uv run lelab --stop`으로 정리한 뒤 다시 실행하세요.

---

## 💡 Windows 환경 핵심 포인트 및 문제 해결

### 1. 포트 자동 인식 (Unplug-to-detect)
로봇 팔(SO-101 리더/팔로워) 연결 시 어떤 시리얼 포트(COM Port)에 연결되었는지 일일이 찾을 필요가 없습니다. USB 케이블을 뽑았다가 다시 꽂으면 화면에서 자동으로 포트를 인식하여 교정(Calibration)을 바로 시작할 수 있습니다.

### 2. 설정 및 교정 파일 저장 경로
Windows에서 교정 데이터 및 설정 파일들은 다음 경로에 안전하게 저장 및 유지됩니다:
- `C:\Users\<사용자이름>\.cache\huggingface\lerobot\calibration\` (리더/팔로워 조인트 설정 등)
- `C:\Users\<사용자이름>\.cache\huggingface\lerobot\ports\` (최근 사용 포트 기록)

### 3. 모바일 카메라 스트리밍 (HTTPS 구성)
스마트폰 카메라를 로봇 학습용 외부 카메라로 사용하려면 브라우저 보안 정책상 **HTTPS** 환경이 필수적입니다. (Windows/Linux 공통)
- self-signed 인증서 파일을 `certs/` 디렉토리에 배치한 후 uvicorn을 수동으로 구동해야 합니다:
  ```bash
  uvicorn lelab.server:app --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem --host 0.0.0.0 --port 8000
  ```

### 4. 허깅페이스 CLI 로그인 (Hugging Face CLI Authentication)
LeLab의 클라우드 GPU 학습 기능(HF Jobs)이나 데이터셋 업로드 기능을 사용하려면 Hugging Face 계정 인증이 필요합니다.

Hugging Face CLI인 `hf` 도구는 **이미 가상환경 내에 설치되어 있습니다**. 따라서 별도의 추가 설치 없이 가상환경 활성화 상태에서 아래 명령어로 로그인할 수 있습니다:

1. **가상환경 활성화 상태**의 터미널에서 아래 명령어를 실행합니다:
   ```powershell
   hf auth login
   ```
2. 프롬프트가 나타나면 [Hugging Face Settings -> Access Tokens](https://huggingface.co/settings/tokens)에서 발급받은 **Access Token (Write 권한 권장)**을 복사하여 붙여넣습니다. (Windows PowerShell에서는 마우스 우클릭으로 붙여넣기가 가능하며, 보안상 입력 중인 문자나 별표는 화면에 표시되지 않습니다.)
3. **로그인 상태 확인**: 제대로 연동이 되었는지 아래 명령어를 통해 로그인 정보를 확인합니다:
   ```powershell
   hf auth whoami
   ```

만약 가상환경 외부(글로벌 환경)에서 개별적으로 Hugging Face CLI를 관리/사용하고 싶다면, 아래와 같이 `uv`를 사용해 글로벌 도구로 설치할 수도 있습니다:
```powershell
# 글로벌 환경에 hf CLI 설치
uv tool install huggingface_hub

# 로그인 실행
hf auth login

# 로그인 확인
hf auth whoami
```

---

## 🐧 Linux 환경 핵심 포인트 및 문제 해결

### 1. 시리얼 포트 접근 권한 (dialout 그룹)
Linux에서 로봇 팔은 `/dev/ttyUSB0`, `/dev/ttyACM0` 같은 장치 파일로 인식되는데, 기본적으로 일반 사용자에게는 접근 권한이 없습니다. `Permission denied` 오류가 나면 사용자를 `dialout` 그룹에 추가하세요:
```bash
sudo usermod -aG dialout $USER
```
> [!IMPORTANT]
> 그룹 변경은 **로그아웃 후 재로그인**(또는 재부팅)해야 적용됩니다. 임시로 바로 쓰려면 `sudo chmod 666 /dev/ttyUSB0`처럼 장치에 직접 권한을 줄 수도 있지만, 재연결 시마다 다시 해줘야 하므로 그룹 추가를 권장합니다.

연결된 포트는 아래 명령어로 확인할 수 있습니다:
```bash
ls /dev/ttyUSB* /dev/ttyACM*
```
포트 자동 인식(Unplug-to-detect) 기능은 Windows와 동일하게 동작합니다 — USB 케이블을 뽑았다 꽂으면 화면에서 자동으로 포트를 찾아줍니다.

### 2. 카메라 (V4L2)
Linux에서는 OpenCV의 **V4L2** 백엔드가 자동으로 사용되며, 카메라 이름은 sysfs(`/sys/class/video4linux/`)에서 읽어옵니다. 별도 패키지는 필요 없지만, 카메라 목록을 직접 확인하고 싶다면:
```bash
sudo apt install v4l-utils
v4l2-ctl --list-devices
```
카메라 접근 권한 오류가 나면 사용자를 `video` 그룹에 추가하세요 (`sudo usermod -aG video $USER`).

### 3. 설정 및 교정 파일 저장 경로
Linux에서 교정 데이터 및 설정 파일들은 다음 경로에 저장됩니다:
- `~/.cache/huggingface/lerobot/calibration/` (리더/팔로워 조인트 설정 등)
- `~/.cache/huggingface/lerobot/ports/` (최근 사용 포트 기록)

### 4. 허깅페이스 CLI 로그인
Windows 섹션의 4번과 동일합니다 — 가상환경 활성화 후 `hf auth login` / `hf auth whoami`를 그대로 사용하면 됩니다.

---

## ❓ 자주 묻는 질문 (FAQ)

### Q. LeLab을 사용하려면 LeRobot을 별도로 설치해야 하나요?
**A. 아닙니다.** LeLab은 `lerobot` 패키지를 의존성으로 포함하고 있으므로, `uv pip install -e .` 명령어를 통해 LeLab을 설치할 때 **자동으로 `lerobot` 라이브러리가 함께 설치**됩니다. 사전에 개별적으로 `lerobot`을 설치하실 필요가 없습니다.

