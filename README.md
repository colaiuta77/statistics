# BookOasis 라이브러리 통계

BookOasis 일반도서 라이브러리를 백그라운드에서 미리 집계하고, 저장된 스냅샷을 ECharts 기반 대시보드로 빠르게 보여주는 독립 카테고리 플러그인입니다.

![BookOasis 통계 대시보드](docs/statistics-dashboard-overview.jpg?v=1.0.0)

![BookOasis 통계 상세 화면](docs/statistics-dashboard-detail.jpg?v=1.0.0)

## 버전 및 호환 정보

| 항목 | 값 |
| --- | --- |
| 플러그인 버전 | `1.0.0` |
| 플러그인 ID | `statistics` |
| 클래스 | `StatisticsMetadataProvider` |
| 모듈 | `plugins.metadata.statistics.statistics` |
| 유형 | 백그라운드 사전 집계형 라이브러리 통계 카테고리 UI 제공자 |
| 확인한 BookOasis 버전 | `2.4.7` |
| 지원 DB | SQLite, MariaDB |
| 현재 지원 세션 | `general` 일반도서 |
| 문서 작성일 | `2026-08-29` |

이 플러그인은 BookOasis의 폴더형 플러그인 구조, `PluginDatabaseGateway`, `category_tab`, `start_background_service()`와 스캔 완료 훅을 사용합니다. BookOasis 공통 UI나 코어 파일을 수정하지 않습니다.

BookOasis `2.4.7`의 최신 `plugins/metadata/base.py`에 포함된 `start_background_service()` 계약이 필요합니다.

## 주요 기능

- BookOasis 시작 후 별도 백그라운드 스레드에서 통계를 사전 집계합니다.
- 통계 화면을 열 때 `books` 전체 통계 쿼리를 다시 실행하지 않고 마지막 정상 스냅샷을 즉시 사용합니다.
- 기본 6시간 주기 자동 갱신을 제공합니다.
- 신규 도서 스캔 감지 후 45초 debounce를 적용해 재집계를 예약합니다.
- 상단 `통계 갱신` 버튼으로 즉시 백그라운드 재집계를 요청할 수 있습니다.
- 전체 보관함과 개별 보관함별 통계를 같은 스냅샷에서 전환합니다.
- 총 도서, 저자, 시리즈, 출판사, 저장 공간, 장르, 보관함, 출판 연도와 올해 추가 도서 KPI를 제공합니다.
- 포맷 분포와 포맷별 저장 공간을 도넛 차트로 표시합니다.
- 메타데이터 평균 완성도, 점수 분포와 누락 현황을 제공합니다.
- 보관함별 메타데이터 완성도 Heatmap은 최대 15개 보관함을 한 화면에 표시하고 내부 세로 스크롤을 제공합니다.
- 장르 분포 Treemap과 장르 동시 출현 Chord를 제공합니다.
- 상위 저자, 상위 시리즈와 상위 출판사를 표시합니다.
- 도서 추가 추이, 출판 시대, 출판 연도 타임라인과 최근 포맷 비중 변화를 제공합니다.
- 페이지 수 분포와 용량이 큰 도서를 표시하며 대용량 도서 축은 MB/GB 단위로 자동 변환합니다.
- Muuri 기반 카드 드래그 정렬, 빈 공간 채우기와 레이아웃 초기화를 제공합니다.
- 카드 순서는 브라우저 `localStorage`에 저장합니다.
- 일반 카드는 1단, Heatmap과 장르 연관성은 2단 높이를 사용해 재배치 시 정렬을 맞춥니다.
- BookOasis 테마 CSS 변수를 사용해 다크/라이트 계열 테마에 맞춰 표시합니다.

## 화면 구성

`statistics`는 `category_tab` 계약을 사용하는 좌측 사이드바의 `통계` 카테고리입니다.

상단에는 전체/개별 보관함 선택, 마지막 집계 시각, 집계 소요 시간, 수동 갱신과 레이아웃 초기화가 표시됩니다. 그 아래에는 KPI와 차트 카드가 배치됩니다.

카드 우측 상단의 그립 아이콘을 드래그하면 원하는 순서로 재배치할 수 있습니다. 데스크톱에서는 Muuri masonry 레이아웃을 사용하고, 작은 화면에서는 단일 열에 가깝게 반응형으로 표시합니다.

시각화 구성은 [BookOrbit](https://github.com/bookorbit/bookorbit)의 통계 대시보드에서 아이디어를 참고했으며, BookOasis 플러그인 계약과 데이터 구조에 맞춰 별도로 구현했습니다.

## 통계 항목

| 영역 | 제공 항목 |
| --- | --- |
| 요약 | 도서, 저자, 시리즈, 출판사, 저장 공간, 장르, 보관함, 출판 연도, 올해 추가 |
| 포맷 | 포맷 분포, 포맷별 저장 공간, 포맷 비중 변화 |
| 메타데이터 | 평균 완성도, 점수 분포, 누락 현황, 보관함별 Heatmap |
| 장르 | 장르 분포 Treemap, 장르 동시 출현 Chord |
| 카탈로그 | 상위 저자, 상위 시리즈, 상위 출판사 |
| 시간 | 도서 추가 추이, 출판 시대, 출판 연도 타임라인 |
| 파일/분량 | 페이지 수 분포, 용량이 큰 도서 |

메타데이터 완성도는 저자, 출판사, 소개, 장르, 태그, ISBN, 표지, 출간일, 페이지 수와 파일 크기의 존재 여부를 기준으로 계산합니다.

`메타데이터 누락 현황`은 기존 완성도 집계 결과의 `전체 건수 - 채워진 건수`로 계산하므로 별도의 도서 전체 스캔 쿼리를 추가하지 않습니다.

## 집계 방식

플러그인 시작 시 `start_background_service()`가 통계 런타임을 시작합니다. 최초 집계는 BookOasis 시작 직후 약간의 지연 뒤 실행되고, 성공한 결과는 다음 위치의 SQLite 스냅샷에 저장됩니다.

```text
plugins/data/statistics/statistics.db
```

화면 요청은 이 스냅샷을 읽은 메모리 상태만 반환합니다. 새로운 집계가 진행 중이거나 실패하더라도 마지막 정상 스냅샷은 유지됩니다.

주요 DB 집계는 `GROUP BY` 중심으로 수행하며 삭제된 도서는 제외합니다.

```text
BookOasis general DB
        │
        │ background aggregation
        ▼
statistics.db snapshot
        │
        ▼
memory snapshot
        │
        ▼
statistics UI / ECharts
```

## 차트 및 레이아웃 라이브러리

프론트엔드는 다음 버전을 HTTPS CDN에서 동적으로 로드합니다.

| 라이브러리 | 버전 | 용도 |
| --- | --- | --- |
| ECharts | `6.1.0` | 도넛, 막대, 라인, Gauge, Heatmap, Treemap, Chord |
| Muuri | `0.9.5` | 드래그 카드 정렬과 masonry 레이아웃 |

BookOasis 기본 CSP는 HTTPS 스크립트를 허용하지만, 관리자가 `SECURITY_CSP_POLICY`를 별도로 제한한 환경에서는 CDN 로딩이 차단될 수 있습니다. 외부 CDN에 접근할 수 없는 환경에서도 플러그인 백그라운드 집계는 동작하지만 차트 렌더링은 제한됩니다.

## 설치

최종 폴더 구조는 다음과 같습니다.

```text
plugins/metadata/
└── statistics/
    ├── __init__.py
    ├── statistics.py
    ├── statistics_core.py
    ├── index.html
    ├── style.css
    ├── script.js
    └── VERSION
```

BookOasis의 `plugins/metadata/`에서 다음 명령을 실행합니다.

```bash
git clone https://github.com/colaiuta77/statistics.git statistics
```

1. BookOasis `2.4.7` 이상과 최신 `plugins/metadata/base.py`가 적용되어 있는지 확인합니다.
2. BookOasis 서버 또는 컨테이너를 재시작합니다.
3. `환경설정 > 플러그인 설정`에서 `통계`가 활성화되어 있는지 확인합니다.
4. 좌측 사이드바의 `통계` 카테고리를 엽니다.
5. 최초 백그라운드 집계가 완료되면 저장된 통계가 표시됩니다.

업데이트할 때는 BookOasis의 `plugins/metadata/`에서 다음 명령을 실행합니다.

```bash
git -C statistics pull --ff-only
```

현재 `1.0.0`은 `update_manifest` 자동 업데이트 계약을 선언하지 않으므로 Git 기반 업데이트를 사용합니다.

Docker 환경에서는 BookOasis `plugins` 디렉터리가 연결된 호스트 볼륨 또는 컨테이너의 동일한 경로에 설치해야 합니다.

## 데이터와 성능

- 현재 버전은 `general` DB의 `libraries`와 `books`를 집계합니다.
- `COALESCE(is_deleted, 0) = 0` 조건으로 삭제된 도서를 제외합니다.
- 전체 통계와 보관함별 통계를 한 번의 백그라운드 집계 과정에서 생성합니다.
- 가능한 통계는 DB의 `COUNT`, `SUM`, `GROUP BY`와 window function으로 집계합니다.
- 장르처럼 문자열 분리가 필요한 데이터만 집계 결과를 Python에서 추가 처리합니다.
- 화면 진입과 보관함 전환은 원본 `books` 통계 쿼리를 다시 수행하지 않습니다.
- 스냅샷 저장소는 원본 BookOasis DB와 분리되어 있으며 통계 데이터만 저장합니다.
- SQLite와 MariaDB의 월별 날짜 집계 표현을 각각 처리합니다.
- 25만 권급 MariaDB 라이브러리에서 백그라운드 집계와 스냅샷 생성 동작을 확인했습니다.

## 제한 사항

- 현재 `1.0.0`은 일반도서 `general` 세션만 지원합니다. 성인도서, 오디오북과 비디오북 통계는 포함하지 않습니다.
- 언어 필드는 현재 BookOasis `books` 데이터 계약에 없어 언어 분포를 제공하지 않습니다.
- 메타데이터 갱신 시각 전용 필드가 없어 BookOrbit 형태의 Metadata Freshness를 동일하게 제공하지 않습니다.
- 실제 파일 시스템을 전체 순회하지 않으므로 물리 파일 존재 여부를 검증하는 Integrity 통계는 제공하지 않습니다.
- 사용자별 독서 세션 통계는 현재 버전에 포함하지 않습니다.
- `created_at`과 `release_date`가 비어 있거나 비표준 형식이면 해당 시간 기반 차트에서 제외될 수 있습니다.
- 장르 연관성은 함께 지정된 상위 장르만 사용하며 모든 장르 조합을 한 화면에 표시하지 않습니다.
- ECharts와 Muuri를 CDN에서 로드하므로 폐쇄망에서는 별도 정적 자산 제공 방식이 필요할 수 있습니다.
- BookOasis의 플러그인 계약 또는 DB 스키마가 변경되면 호환성 업데이트가 필요할 수 있습니다.

## 검증

```bash
python3 -m py_compile __init__.py statistics.py statistics_core.py
node --check script.js
```

개발 과정에서는 백그라운드 스냅샷, MariaDB SQL dialect, 보관함별 scope, 메타데이터 누락 계산, Heatmap 스크롤, MB/GB 축 변환과 드래그 카드 레이아웃에 대한 회귀 테스트를 수행했습니다. 개발용 테스트와 NAS 적용 스크립트는 GitHub 배포본에 포함하지 않습니다.

## 변경 이력

### 1.0.0 - 2026-08-29

- BookOasis `2.4.7`의 `start_background_service()`를 이용한 사전 통계 집계 추가.
- 전체/보관함별 라이브러리 통계와 영구 스냅샷 저장 구조 추가.
- ECharts `6.1.0` 기반 도넛, Gauge, 막대, 라인, Heatmap, Treemap과 Chord 차트 추가.
- Muuri `0.9.5` 기반 드래그 카드 정렬, 순서 저장과 레이아웃 초기화 추가.
- 1단/2단 카드 높이 체계와 보관함 Heatmap 내부 세로 스크롤 추가.
- 메타데이터 완성도, 점수 분포와 누락 현황 추가.
- 파일 크기 차트 MB/GB 자동 단위 표시 추가.
- 실제 BookOasis 다크 테마 화면을 README에 추가.

## 라이선스

이 저장소의 [LICENSE](LICENSE)를 따릅니다.
