# 地標落客座標 — 人手覆核清單 / Landmark Drop-off Coordinates

> **EN — Summary.** A manual review checklist, not authoritative data. Every
> coordinate here passes the project's own `is_in_hong_kong()` boundary check —
> but **passing the boundary check does not make a coordinate a legal drop-off
> point.**
>
> **The distinction this document exists to make**: "the geometric centre of a
> landmark" is not "a place a taxi can stop". A boundary test can only answer
> whether somewhere is inside Hong Kong; it cannot answer whether a vehicle may
> legally stop there. Someone has to look at each one.
>
> **中文摘要**：這是**人手覆核清單，不是權威資料**。清單上的座標本身都通過專案自身的
> `is_in_hong_kong()` 邊界檢查，但**通過邊界檢查不等於可以合法落客**。
> 本文存在的意義正是這個區別：「地標的幾何中心」不等於「的士可以停車落客的位置」。

> **用途**：這份清單供人手（或營運同事）用 Google Maps 逐個覆核落客位置。
> 座標本身已通過專案自身的 `is_in_hong_kong()` 邊界檢查，
> 但**「地標的幾何中心」不等於「的士可以停車落客的位置」** ——
> 後者必須由人判斷。詳見 `docs/IN_TRIP_REDESIGN.md` §9 DECISION-7。
>
> 座標來源：OpenStreetMap（社群維護資料）。全部為 WGS84。
>
> **狀態（2026-10-01）**：19 個地標全部通過邊界檢查，**無待修項**。
> 深圳灣口岸的邊界缺陷已修好（見 §二）。

---

## 一、一般地標（19 個，已通過邊界檢查）

每一項的 Google Maps 連結都已用該座標預先定位，點開即可看到實際位置。

> **2026-10-01 更新**：原本 18 個 + 深圳灣口岸 1 個「待修」。深圳灣口岸的邊界缺陷
> **已修好**（見 §二），口岸座標現已可通過 `is_in_hong_kong()`，所以總數由
> 18 個待修清單變成 **19 個即用**。口岸一項列在 §一 的表末。

### 機場與場館

| `code` | 名稱 | 類別 | 座標 (lat, lng) | 建議 `radius_m` | 邊界檢查 | Google Maps |
|---|---|---|---|---|---|---|
| `HKIA` | 香港國際機場 | `AIRPORT` | 22.312599, 113.917300 | 800 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.312599,113.917300) |
| `ASIAWORLD` | 亞洲國際博覽館 | `VENUE` | 22.321251, 113.942968 | 400 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.321251,113.942968) |
| `HK_CONVENTION` | 香港會議展覽中心 | `VENUE` | 22.282625, 114.173069 | 300 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.282625,114.173069) |
| `HK_COLISEUM` | 香港體育館（紅館） | `VENUE` | 22.301318, 114.181981 | 300 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.301318,114.181981) |
### 主題公園

| `code` | 名稱 | 類別 | 座標 (lat, lng) | 建議 `radius_m` | 邊界檢查 | Google Maps |
|---|---|---|---|---|---|---|
| `DISNEYLAND` | 香港迪士尼樂園 | `THEME_PARK` | 22.313070, 114.040985 | 600 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.313070,114.040985) |
| `OCEAN_PARK` | 海洋公園 | `THEME_PARK` | 22.234767, 114.170817 | 600 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.234767,114.170817) |

### 商場與寫字樓

| `code` | 名稱 | 類別 | 座標 (lat, lng) | 建議 `radius_m` | 邊界檢查 | Google Maps |
|---|---|---|---|---|---|---|
| `ICC` | 環球貿易廣場 | `OFFICE` | 22.303379, 114.160226 | 200 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.303379,114.160226) |
| `IFC` | 國際金融中心 | `OFFICE` | 22.285163, 114.159815 | 200 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.285163,114.159815) |
| `HARBOUR_CITY` | 海港城 | `MALL` | 22.297002, 114.168420 | 300 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.297002,114.168420) |
| `TIMES_SQ` | 時代廣場 | `MALL` | 22.278359, 114.182106 | 200 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.278359,114.182106) |

### 海旁

| `code` | 名稱 | 類別 | 座標 (lat, lng) | 建議 `radius_m` | 邊界檢查 | Google Maps |
|---|---|---|---|---|---|---|
| `TST_PROMENADE` | 尖沙咀海旁 | `WATERFRONT` | 22.299419, 114.185648 | 500 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.299419,114.185648) |
| `KT_PROMENADE` | 觀塘海旁 | `WATERFRONT` | 22.312288, 114.217369 | 400 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.312288,114.217369) |

### 口岸

| `code` | 名稱 | 類別 | 座標 (lat, lng) | 建議 `radius_m` | 邊界檢查 | Google Maps |
|---|---|---|---|---|---|---|
| `HZMB_PORT` | 港珠澳大橋香港口岸 | `BORDER` | 22.317868, 113.954070 | 500 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.317868,113.954070) |
| `LOK_MA_CHAU` | 落馬洲支線管制站 | `BORDER` | 22.515276, 114.065632 | 400 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.515276,114.065632) |
| `LO_WU` | 羅湖管制站 | `BORDER` | 22.529713, 114.113850 | 300 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.529713,114.113850) |
| `MAN_KAM_TO` | 文錦渡管制站 | `BORDER` | 22.519218, 114.124584 | 300 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.519218,114.124584) |
| `SHENZHEN_BAY` | 深圳灣口岸（港方口岸區）公共運輸交匯處 | `BORDER` | 22.500992, 113.945654 | 400 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.500992,113.945654) |

### 醫院

| `code` | 名稱 | 類別 | 座標 (lat, lng) | 建議 `radius_m` | 邊界檢查 | Google Maps |
|---|---|---|---|---|---|---|
| `QMH` | 瑪麗醫院 | `HOSPITAL` | 22.269875, 114.131214 | 300 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.269875,114.131214) |
| `PWH` | 威爾斯親王醫院 | `HOSPITAL` | 22.379609, 114.202193 | 300 | ✅ | [開啟](https://www.google.com/maps/search/?api=1&query=22.379609,114.202193) |

---

## 二、深圳灣口岸 — **已修好（選項 A）**

### 落客點：深圳灣口岸公共運輸交匯處

| 項目 | 值 |
|---|---|
| 名稱 | 深圳灣口岸（港方口岸區）公共運輸交匯處 |
| OSM 座標 | **22.500992, 113.945654** |
| Wikimapia 座標 | **22°30'5"N 113°56'41"E** = 22.501390, 113.944720（相差約 100 米，互相印證） |
| OSM 定位 | `bus_station`（way 581117553），位於「港深西部公路（Kong Sham Western Highway）」 |
| Google Maps | [開啟](https://www.google.com/maps/search/?api=1&query=22.500992,113.945654) |
| **`is_in_hong_kong()` 結果** | ✅ **True（已修好）** |

### 為何這個點「在深圳境內，卻必須判為香港」

這一條不是普通的邊界鬆弛，而是**法律上正確**的結果，值得寫清楚：

- 深圳灣口岸分兩部分：**旅檢大樓北部及相連車站由深圳市管轄**；
  **旅檢大樓南部及相連的公共運輸交匯處屬「港方口岸區」**。
- 全國人大常委會 2006-10-31 授權香港特區在口岸內**實行全封閉式管理、實施香港法律**；
  國務院同年 12-30（國函〔2006〕132號）批覆港方口岸區範圍及土地使用期限。
- 港方口岸區佔地 **41.565 公頃**，土地**由深圳市政府擁有**，香港以**租賃**方式取得、
  **每年支付租金**，期限至 **2047-06-30**。
- 用一句話概括它的性質：**不在香港土地，但在香港境內。**
  （該交匯處自述為「香港管轄範圍內唯一並非位處香港土地的車站」。）

所以一個站在港方口岸區的裝置，其用戶**有權使用這個 app** ——
無論是去口岸落客的司機，還是被送到口岸的乘客。把它判為境外是
一個有明確後果的 false negative。

### 原本的缺陷（已修）

修好前，`app/core/hk_bounds.py` 的 `_HK_MAIN` 在后海灣一段只有兩個頂點
（`22.4600,113.9200` → `22.5050,113.9600`），斜率 `dlat/dlng = 1.125`。
在交匯處的經度 113.945654 上，那條邊的緯度只有 **22.488861**，
而交匯處在 **22.500992** —— **低了 0.0121°（約 1.34 公里）**。

後果：一輛開往深圳灣口岸的的士，在**距離目的地還有約 1.3 公里時
就已經「離開香港」**，訂單在建立時會以 422 `OUTSIDE_HK` 被拒。

### 修法：只把「港方口岸區」這一塊挑出來

`_HK_MAIN` 的 Deep Bay 段由兩個頂點改成七個，**只在口岸一帶向北繞一個彎**：

```
LatLng(22.4600, 113.9200),  # 鰲磡石（不變）
LatLng(22.4830, 113.9330),  # 后海灣，向口岸爬升
LatLng(22.4880, 113.9380),  # 深圳灣大橋港方段
LatLng(22.4940, 113.9400),  # 橋頭引道
LatLng(22.5010, 113.9412),  # 口岸區西牆（交匯處以西約 450 米）
LatLng(22.5055, 113.9440),  # 口岸區東北，越過交匯處
LatLng(22.5070, 113.9500),  # 口岸區北牆，回接深圳河
LatLng(22.5050, 113.9600),  # 后海灣東側（不變）
LatLng(22.5150, 114.0200),  # 深圳河口（不變）
```

**西牆刻意留了約 450 米餘量**：交匯處本身有面積，加上 GPS 誤差，
餘量太薄會令真實的落客點仍然掉出境外。

**驗證（實跑，非推算）：**

- 陽性（必須 True）：交匯處（OSM / Wikimapia 兩個來源）、交匯處東南西北各 250 米、
  港方口岸區本體、深圳灣大橋港方落腳點、鰲磡石、尖鼻咀、天水圍、元朗、屯門、
  龍鼓灘、落馬洲、羅湖、文錦渡、機場、港珠澳大橋香港口岸、沙頭角、東平洲、長洲
  —— **全部 True**。
- 陰性（必須 False）：蛇口碼頭、蛇口海上世界、蛇口北、蛇口東北、蛇口東岸、
  **深圳灣口岸（深圳側）管制站**、深圳灣口岸北側、南山、前海、后海灣北面水域、
  福田、羅湖（深圳）、鹽田、寶安、華強北、市民中心 —— **全部 False**。
- **網格掃描**：在整片后海灣 / 蛇口角上以 0.0025° 步長掃 212 個境內點，
  確認沒有任何一點落到「管制站以北」或「蛇口東岸以西」的深圳一側。

測試見 `tests/domain/test_hk_bounds.py`：`test_the_shenzhen_bay_port_area_defect_cannot_come_back`
（鎖死舊的兩頂點邊界）與 `test_admitting_the_port_area_did_not_admit_shenzhen`
（網格性質測試）。後者已用兩個故意的錯誤邊界驗證過**會 fail**，不是空轉。

> ⚠️ **改動此段前必讀**：`_HK_MAIN` 這段是全模組唯一一處刻意伸到深圳河
> 走廊以北。它靠的不是「放寬」，而是「**只在港方口岸區這一小塊**放寬」。
> 若要再動，先跑 `tests/domain/test_hk_bounds.py`。

---

## 三、覆核方法建議

逐個點開上表的 Google Maps 連結，用**街景（Street View）**確認：

1. **該位置是否有的士可以停車上落客？**（黃格、的士站、雙黃線會否妨礙？）
2. **司機導航過去時，會不會被導到一個進不去的入口？**（例如商場停車場入口、
   大廈私家路、酒店迴旋處）
3. 若是機場 / 迪士尼 / 紅館這類**有專用的士上落客區**的地方，
   座標是否應該改到那個區域？

建議特別留意的幾個：

| `code` | 為甚麼要特別看 |
|---|---|
| `HKIA` | 客運大樓幾何中心 vs **的士落客區**，可能相差數百米 |
| `DISNEYLAND` | 應用的士上落客區，不是樂園正門廣場 |
| `HK_COLISEUM` | 應對**暢運道**對出，不是體育館幾何中心 |
| `HARBOUR_CITY` | 商場極大，要選一個的士能停的**具體門** |
| `TST_PROMENADE` | 海岸線很長，`radius_m = 500` 可能仍偏窄 |
| `OCEAN_PARK` | 有正門與大樹灣兩個入口，需指定一個 |
| `LO_WU` / `LOK_MA_CHAU` | 管制站有多層，落客點應在**的士站**而非禁區內 |
