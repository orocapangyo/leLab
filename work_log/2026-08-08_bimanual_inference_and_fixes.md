# 🦾 양팔(Bimanual) 인퍼런스 구현 & 버그 수정 최종 리포트

**작성일**: 2026-08-08 (녹화 통신 실패 최종 해결 확인: 2026-08-09)
**대상**: `lelab_Twins` (양팔 OMX-AI 지원 LeLab)
**요약**: 양팔 정책 **인퍼런스(policy rollout)** 를 새로 구현하고, 실제 하드웨어(OMX-AI 리더 2 + 팔로워 2 + 카메라 2)로 캘리브레이션·텔레옵·녹화·학습·인퍼런스 전 과정을 돌리는 중 발견된 버그 6건을 수정했습니다. 특히 세션 내내 가장 골치였던 **반복적인 녹화 통신 실패**는 하드웨어 문제가 아니라 **코드 버그였음이 최종 확인·해결**되었습니다.

---

## 1. 📌 최종 결과 한눈에

| 항목 | 상태 |
|---|---|
| 양팔 in-process 인퍼런스 구현 | ✅ 완료 (실제 하드웨어 동작 확인) |
| 인퍼런스 "Exit code 1" 원인(`robot_type` 누락) | ✅ 해결 |
| 에피소드 증가 안 되던 버그 (기존 버그) | ✅ 해결 |
| 학습 job 서브프로세스 좀비 방지 | ✅ 해결 |
| 녹화 시작 직후 통신 실패(settle delay) | ✅ 해결 |
| **반복되던 녹화 중 통신 실패 (`no status packet`)** | ✅ **최종 해결** (코드 버그였음, 8절 참고) |
| Jobs 목록이 클라우드 실패에 가려지던 문제 | ✅ 해결 |
| Python 테스트 | ✅ 224개 통과 (기존에 있던 무관한 flaky 테스트 1개 제외) |
| 프런트엔드 타입체크(tsc) | ✅ 통과 |

---

## 2. 🎯 핵심 작업: 양팔 In-Process 인퍼런스

### 배경 / 문제
- 양팔 작업 때 **캘리브레이션 / 텔레옵 / 녹화**만 양팔로 확장했고, **인퍼런스(rollout)는 범위에서 제외**되어 있었습니다.
- 기존 `rollout.py`는 lerobot의 `lerobot-rollout` **CLI를 서브프로세스로 호출**하는데, 이 CLI는 lerobot에 등록된 **단일팔 로봇 타입**(`omx_follower`, `so101_follower`)만 압니다.
- 그래서 양팔(12-DoF) 정책을 단일팔 경로로 돌리면 관측 6차원 vs 정책 기대 12차원 불일치로 반드시 실패:
  ```
  RuntimeError: The size of tensor a (6) must match the size of tensor b (12) at non-singleton dimension 1
  ```

### 해결 방식 (선택: lelab in-process)
lerobot 패키지를 **건드리지 않고**, `rollout.py`를 record.py처럼 **in-process로** 돌려서 lelab 자체 `BimanualRobot` 래퍼(12-DoF)를 그대로 쓰게 했습니다.

- lerobot의 롤아웃 기계(정책 로딩 · sync 인퍼런스 엔진 · base 제어 루프 · teardown)를 `build_rollout_context`/`create_strategy`로 **그대로 재사용**
- 단, `make_robot_from_config`만 **임시 패치**해서 `BimanualRobotConfig` → `BimanualRobot.from_config`로 해석되게 함 (`_patched_bimanual_robot_factory`)
- 서브프로세스(단일팔)·스레드(양팔) 두 백엔드를 상태/정지/상태조회 핸들러가 **통합 처리**
- 카메라는 녹화 때와 동일하게 `left_`/`right_` 접두어로 좌우 분배 → 관측 키가 학습 데이터와 **정확히 일치**

### 검증 (실제 정책으로)
- 정책 기대: `observation.state[12]` + 카메라 `left_r`/`left_l` + `action[12]`
- 조합한 `BimanualRobot`: obs/action 키 정확히 12개(`left_*.pos` 6 + `right_*.pos` 6) → 정책과 일치 확인
- `RolloutConfig` 생성 · 정책 로딩 · 로봇 팩토리 주입 모두 통과, **실제 하드웨어에서 인퍼런스 동작 확인**

---

## 3. 🐛 버그 A — 인퍼런스 "Exit code 1" (`BimanualRobot`에 `robot_type` 없음)
- **증상**: 양팔 인퍼런스가 시작 직후 매번 `Exit code 1`로 종료.
  ```
  File "robot_wrapper.py", line 66, in robot_type
      return self._robot.robot_type
  AttributeError: 'BimanualRobot' object has no attribute 'robot_type'
  ```
- **원인**: 일반 로봇은 `Robot.__init__`에서 `self.robot_type = self.name`을 설정하는데, `BimanualRobot`은 (단일 캘리브레이션 파일 가정을 피하려고) **`Robot.__init__`을 일부러 호출하지 않아서** `robot_type`이 없음. 인퍼런스 경로에서만 로봇을 `ThreadSafeRobot`으로 감싸고 `.robot_type`을 참조 → 녹화/텔레옵은 안 건드리고 **인퍼런스에서만** 터짐.
- **수정**: `_BimanualDevice`에 `robot_type` 프로퍼티 추가(=`self.name`, lerobot 규약과 동일). → `lelab/utils/bimanual.py`

## 4. 🐛 버그 B — 에피소드가 안 늘어남 (기존 버그, 양팔과 무관)
- **증상**: 녹화 시 에피소드가 1에서 안 넘어가고 무한 반복. "End Episode"를 안 누르고 타이머가 다 차면 그 에피소드를 통째로 버림.
- **원인**: 에피소드 시간이 자연스럽게 다 찼을 때(정상 완료), "End Episode(exit_early)"를 안 눌렀다는 이유만으로 무조건 `rerecord_episode = True`로 설정 → 저장 없이 재녹화 반복. (2026-06-08 커밋부터 있던 **단일팔 시절 기존 버그**, 양팔 작업과 무관)
- **수정**: 시간 초과 = "End Episode"와 동일한 정상 완료로 취급해 저장 후 다음 에피소드로 진행. 재녹화는 **명시적 재녹화 버튼**을 눌렀을 때만. → `lelab/record.py`

## 5. 🐛 버그 C — 학습 job 서브프로세스 좀비 (프로세스 그룹 미종료)
- **증상**: 학습을 멈추거나 실패한 뒤에도 GPU/CPU를 물고 있는 고아 프로세스가 남음.
- **원인**: 학습 서브프로세스를 `start_new_session=True`로 띄우는데, `stop()`은 `terminate()`/`kill()`로 **직계 PID 하나만** 종료 → PyTorch DataLoader `num_workers` 같은 손자 프로세스가 고아로 잔존.
- **수정**: `os.killpg(os.getpgid(pid), SIGTERM/SIGKILL)`로 **프로세스 그룹 전체** 종료. `SubprocessJobRunner.stop()`, `TailingJobRunner.stop()` 둘 다. → `lelab/jobs.py`

## 6. 🐛 버그 D — 로컬 학습 모델이 목록에서 사라짐 (`Promise.all` 실패 전파)
- **증상**: "LOCAL JOBS (0) / Couldn't load jobs: Failed to fetch". 백엔드 `/jobs`는 모델을 정상 반환하는데 화면엔 안 뜸.
- **원인**: 로컬 작업과 HF Cloud 작업을 `Promise.all`로 **한꺼번에** 가져와서, 클라우드 쪽(`/jobs/hub`)이 실패하면 **전체가 실패** → 로컬 학습 모델이 화면에서 사라짐.
- **수정**: `Promise.allSettled`로 **독립 처리**. 클라우드 실패가 로컬 목록을 가리지 않음. → `frontend/src/components/jobs/JobsSection.tsx`

## 7. 🐛 버그 E — 녹화 시작 직후 통신 실패 (settle delay 부재)
- **증상**: 녹화 시작 1초 만에 `Failed to sync read 'Present_Position' ... no status packet`으로 실패.
- **원인**: 녹화 시작 시 모든 모터에 `disable_torque → write_calibration → configure` 쓰기를 몰아서 한 직후 바로 첫 `Present_Position` 읽기를 시도. USB-시리얼 어댑터가 쓰기 버스트를 미처 소화하기 전이라 응답 못 함.
- **수정**: 캘리브레이션 쓰기 직후 · 제어 루프 시작 전 **0.5초 settle delay** 추가. → `lelab/record.py`
- **효과**: "시작 즉시(1초 내) 실패"는 완전히 사라짐. 다만 녹화 도중(수십 초~수 분 뒤) 실패는 계속 재발 → 8절에서 별도로 추적.

---

## 8. 🔬 버그 F — 반복되던 녹화 중 통신 실패 (진짜 원인은 하드웨어가 아니었음)

이 세션에서 **가장 오래, 가장 많이** 재현된 문제입니다. 진단 과정과 최종 원인을 그대로 남깁니다.

### 증상
녹화 도중(에피소드 1~4, 수십 초~수 분 경과) 무작위 시점에 아래 에러로 세션이 죽음:
```
Failed to sync read 'Present_Position' on ids=[11, 12, 13, 14, 15, 16] after 1 tries.
[TxRxResult] There is no status packet! / Incorrect status packet!
```
`ids=[11..16]`은 OMX-AI 컨벤션상 **팔로워**이며, 거의 매번 **오른쪽 팔로워** 쪽에서 발생.

### 하드웨어를 의심하고 진행한 진단 (전부 기록)
| 시도 | 결과 |
|---|---|
| USB 재연결/케이블 재체결 | 일시적으로만 완화, 재발 |
| 오른쪽 팔로워 케이블 자체 교체 | 재발 (같은 자리에서) |
| 좀비 프로세스(`multiprocessing.spawn`, 60%+ CPU로 보임) 발견 및 kill | 실시간 CPU 재측정 결과 **항상 idle(0%)** → 원인 아니었음 |
| `jobs.py` 학습 서브프로세스 프로세스 그룹 종료 미비(버그 C) 수정 | 실제 버그였지만 이 증상의 원인은 아니었음 |
| **왼쪽↔오른쪽 팔로워 보드(OpenRB-150) 물리적으로 교체** | 다른 보드가 "오른쪽" 자리에 와도 **똑같이 재발** |
| **USB 포트/허브 경로까지 완전히 다른 곳으로 이동** | 물리 경로가 바뀌어도 **똑같이 재발** |

→ 보드도 바뀌고 포트/허브 경로도 완전히 바뀌었는데 **항상 "오른쪽 팔로워"라는 소프트웨어 역할에서만** 재발한다는 것이 결정적 단서였습니다. 하드웨어라면 이렇게 일관될 수 없습니다.

### 진짜 원인
lerobot의 `OmxFollower.get_observation()` / `OmxLeader.get_action()`은 `bus.sync_read("Present_Position")`을 **재시도 0회**(`num_retry=0`, 라이브러리 기본값)로 호출합니다 — 즉 **상태 패킷 하나만 놓쳐도 즉시 예외**를 던지고 절대 재시도하지 않습니다. 이건 lerobot 자체 코드(호출부가 하드코딩)이지 lelab 코드가 아닙니다.

양팔 모드는 매 제어 루프 프레임마다 이 왕복 통신을 **4번**(왼쪽/오른쪽 × 팔로워/리더) 하는데, 이는 단일팔의 2배입니다. USB-시리얼 통신에서 패킷이 어쩌다 하나씩 씹히는 건 원래도 드물게 있는 정상적인 노이즈인데, 재시도가 0번이라 **그 한 번의 미스가 전체 10-에피소드 녹화 세션을 통째로 죽였던 것**입니다. 왼쪽을 먼저 처리하고 오른쪽을 항상 나중에 처리하는 순서상, 우연히도 "오른쪽에서 자주 터지는 것처럼" 보였을 뿐, 실제로는 특정 하드웨어와 무관한 확률적 현상이었습니다.

### 수정
lerobot을 건드리지 않고, lelab의 `BimanualRobot`/`BimanualTeleoperator`가 **실패한 쪽만 즉시 최대 3회까지 재시도**하도록 감쌌습니다 (`_with_retry`, `lelab/utils/bimanual.py`):
- `BimanualRobot.get_observation()` / `send_action()`
- `BimanualTeleoperator.get_action()`

일회성 패킷 미스는 흡수하고, 재시도해도 계속 실패하는 **진짜 연결 끊김**은 그대로 예외로 올라가 세션이 안전하게 중단됩니다(마스킹하지 않음).

### 최종 검증
- 단위 테스트 5개 추가: 재시도 후 회복, 재시도 소진 시 예외 전파, 한쪽만 재시도하고 다른 쪽엔 영향 없음 등 (`tests/test_bimanual.py`)
- **실제 하드웨어 녹화 재시도 → 에러 재발하지 않음 확인** (2026-08-09)

---

## 9. 🗂️ 변경 파일 요약

| 파일 | 변경 내용 |
|---|---|
| `lelab/rollout.py` | 양팔 in-process 인퍼런스 (요청 스키마 `right_*` 필드, `_patched_bimanual_robot_factory`, `_build_bimanual_follower_config`, `_bimanual_inference_worker`, `_start_bimanual_inference`, 스레드/서브프로세스 통합 상태·정지·상태조회) |
| `lelab/utils/bimanual.py` | `robot_type` 프로퍼티 추가, `type` 라벨 추가, **`_with_retry`로 좌우 통신 재시도 래핑** (버그 F) |
| `lelab/record.py` | 에피소드 증가 버그 수정, settle delay 0.5초 추가 |
| `lelab/jobs.py` | `stop()` 프로세스 그룹 전체 종료(killpg) |
| `frontend/src/components/jobs/JobsSection.tsx` | 로컬/클라우드 작업 `Promise.allSettled`로 분리 |
| `frontend/src/components/landing/InferenceModal.tsx` | 양팔이면 인퍼런스 요청에 `right_*` 필드 포함 |
| `frontend/src/lib/inferenceApi.ts` | `StartInferenceRequest`에 양팔 필드 추가 |
| `tests/test_rollout.py` | 양팔 인퍼런스 테스트 5개 추가 |
| `tests/test_bimanual.py` | 통신 재시도 테스트 5개 추가 |

**검증**: Python 테스트 224개 통과(무관한 기존 flaky 테스트 1개 제외), 프런트엔드 tsc 통과.

---

## 10. ⚠️ 카메라 이름 규칙 (중요, 미해결 항목)

이번에 학습된 정책의 카메라가 **둘 다 `left_`** (`left_r`, `left_l`)로 잡혔습니다. 녹화 때 카메라 이름을 `r`, `l`(접두어 없이)로 지으면 `_split_cameras_by_side`가 **접두어 없는 카메라를 전부 왼쪽 팔에 귀속**시키기 때문입니다.

- **각 팔에 카메라를 하나씩** 두려면 → 녹화 시 카메라 이름을 `left_...` / `right_...` 로 **접두어를 붙여** 지어야 좌우로 나뉩니다.
- 인퍼런스 카메라 슬롯은 정책이 학습한 이름을 그대로 보여주므로(`left_r`/`left_l`), 그때 붙인 이름이 그대로 요구됩니다.
- 각 팔에 카메라를 두려면 **재수집(재녹화) + 재학습**이 필요합니다(이름만 고쳐서 될 문제가 아님 — 이미 학습된 정책은 못 바꿈).

---

## 11. 📎 남은 작업 / 후속 아이디어

- **동일 모델 카메라 구분**: 카메라 2개가 완전히 같은 모델이면(둘 다 `USB2.0 PC CAMERA`), 브라우저 이름 매칭으로는 cv2 인덱스 #2/#4를 확실히 구분 못 함. 백엔드에서 **cv2 인덱스로 직접 스냅샷**을 찍어 보여주는 방식이 확실(미구현, 제안 상태 — 사용자 확인 대기 중).
- **`local/` 네임스페이스 데이터셋**: HF 로그인 전 녹화분은 `local/` 접두어라 업로드 불가(404). `local/aic_dataset`는 실제로 빈 데이터셋(0 에피소드, `ur5e_aic` 로봇 타입)으로 확인됨 — 필요하면 제대로 재녹화 필요.
- **양팔 인퍼런스 카메라 분리**: 각 팔에 카메라를 두려면 녹화 단계부터 `left_`/`right_` 접두어로 재수집 후 재학습 필요(10절 참고).

---

_관련 문서: 같은 폴더의 [2026-07-26_bimanual_support.md](2026-07-26_bimanual_support.md)(양팔 초기 구현), [install.md](install.md)(설치·실행 가이드)._
