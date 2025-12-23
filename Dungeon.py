import os
import sys
import random
import pygame as pg
import math

WIDTH = 1100
HEIGHT = 650
FPS = 60

# デバッグ：地面ラインを表示するなら True
DEBUG_DRAW_GROUND_LINE = True

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ステージ2へ移行するフレーム（仕様に明記が無いので仮定：25秒相当）
STAGE2_TMR = 1500  # 60FPS想定

# グローバル（現在ステージの接地Y）
GROUND_Y = HEIGHT - 60

# ===== HP/ダメージ（追加）=====
HP_MAX = 100
DMG = 20
POPUP_FRAMES = 120   # 約2秒（60FPS想定）
INV_FRAMES = 30      # 無敵0.5秒（接触中に毎フレーム減るのを防ぐための実装上の仮定）

# ===== 右下UI（Attack/Status）（追加）=====
BOX_W, BOX_H = 220, 110
BOX_GAP = 14
BOX_MARGIN = 20

PERSIST_ITEM_LEVEL = False

# =========================
# クラス外関数（メモ準拠）
# =========================
def load_image(filename: str) -> pg.Surface:
    """
    画像読み込み（fig/filename -> filename の順に探す）
    """
    candidates = [os.path.join("fig", filename), filename]
    last_err = None
    for path in candidates:
        try:
            return pg.image.load(path).convert_alpha()
        except Exception as e:
            last_err = e
    raise SystemExit(f"画像 '{filename}' の読み込みに失敗しました: {last_err}")


def check_bound(obj_rct: pg.Rect) -> tuple[bool, bool]:
    yoko, tate = True, True
    if obj_rct.left < 0 or WIDTH < obj_rct.right:
        yoko = False
    if obj_rct.top < 0 or HEIGHT < obj_rct.bottom:
        tate = False
    return yoko, tate


def clamp_in_screen(rect: pg.Rect) -> pg.Rect:
    rect.left = max(0, rect.left)
    rect.right = min(WIDTH, rect.right)
    rect.top = max(0, rect.top)
    rect.bottom = min(HEIGHT, rect.bottom)
    return rect


def get_ground_y() -> int:
    """
    現在ステージの地面Y
    """
    return GROUND_Y


def set_ground_y(v: int) -> None:
    global GROUND_Y
    GROUND_Y = v


def stage_params(stage: int) -> dict[str, int | str]:
    """
    ステージごとの設定

     Returns:
        dict[str, int | str]:
            "bg_file" (str): 背景画像ファイル名
            "bg_speed" (int): 背景スクロール速度
            "enemy_speed" (int): 敵の移動速度
            "item_speed" (int): アイテムの移動速度
            "spawn_interval" (int): 敵の生成間隔（フレーム）
    
    """
    if stage == 1:
        return {
            "bg_file": "bg_1.jpg",
            "bg_speed": 4,
            "enemy_speed": 7,
            "item_speed": 5,
            "spawn_interval": 60,  # フレーム間隔
        }
    return {
        "bg_file": "bg_2.jpg",
        "bg_speed": 6,
        "enemy_speed": 9,
        "item_speed": 7,
        "spawn_interval": 45,
    }


def should_switch_stage(tmr: int) -> bool:
    """
    ステージ2へ移行する条件（仕様が無いので仮定：一定時間）
    """
    return tmr >= STAGE2_TMR


def spawn_enemy(enemies: pg.sprite.Group, stage: int) -> None:
    enemies.add(Enemy(stage))


def detect_ground_y(bg_scaled: pg.Surface) -> int:
    """
    リサイズ済み背景から「暗くて横方向に均一な水平ライン」を推定し、
    その“1px下”を地面Yとして返す。
    """
    w, h = bg_scaled.get_size()

    y_start = int(h * 0.40)
    y_end = int(h * 0.90)

    x_step = 4
    best_y = int(h * 0.75)
    best_score = 10**18

    for y in range(y_start, y_end):
        s = 0.0
        s2 = 0.0
        n = 0
        for x in range(0, w, x_step):
            r, g, b, a = bg_scaled.get_at((x, y))
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            s += lum
            s2 += lum * lum
            n += 1

        mean = s / n
        var = (s2 / n) - mean * mean
        std = (var ** 0.5) if var > 0 else 0.0

        score = mean + 0.3 * std
        if score < best_score:
            best_score = score
            best_y = y

    return min(h - 1, best_y + 1)


# =========================
# クラス
# =========================
class Background:
    """
    背景を右→左へ強制スクロール（2枚並べてループ）
    """
    def __init__(self, bg_file: str, speed: int):
        raw = load_image(bg_file)
        self._img = pg.transform.smoothscale(raw, (WIDTH, HEIGHT))
        self._speed = speed
        self._x1 = 0
        self._x2 = WIDTH
        set_ground_y(detect_ground_y(self._img))

    def update(self, screen: pg.Surface):
        self._x1 -= self._speed
        self._x2 -= self._speed

        if self._x1 <= -WIDTH:
            self._x1 = self._x2 + WIDTH
        if self._x2 <= -WIDTH:
            self._x2 = self._x1 + WIDTH

        screen.blit(self._img, (self._x1, 0))
        screen.blit(self._img, (self._x2, 0))


class Bird(pg.sprite.Sprite):
    """
    プレイヤー：左右移動＋ジャンプ＋二段ジャンプ
    """
    def __init__(self, num: int, xy: tuple[int, int]):
        super().__init__()
        img0 = pg.transform.rotozoom(load_image(f"{num}.png"), 0, 0.9)
        img = pg.transform.flip(img0, True, False)

        self._imgs = {+1: img, -1: img0}
        self._dir = +1

        self.image = self._imgs[self._dir]
        self.rect = self.image.get_rect()

        # 物理（ここは一切変更しない）
        self._vx = 0
        self._vy = 0.0
        self._speed = 8
        self._gravity = 0.85
        self._jump_v0 = -15
        self._jump_count = 0
        self._max_jump = 2

        self.rect.center = xy
        self.rect.bottom = get_ground_y()

    def try_jump(self) -> None:
        if self._jump_count < self._max_jump:
            self._vy = self._jump_v0
            self._jump_count += 1

    def update(self, key_lst: list[bool], screen: pg.Surface) -> None:
        # 左右入力
        self._vx = 0
        if key_lst[pg.K_LEFT]:
            self._vx = -self._speed
            self._dir = -1
        if key_lst[pg.K_RIGHT]:
            self._vx = +self._speed*0.5
            self._dir = +1

        self.rect.x += self._vx
        self.rect = clamp_in_screen(self.rect)

        self._vy += self._gravity
        self.rect.y += int(self._vy)

        gy = get_ground_y()
        if self.rect.bottom >= gy:
            self.rect.bottom = gy
            self._vy = 0.0
            self._jump_count = 0

        self.image = self._imgs[self._dir]
        screen.blit(self.image, self.rect)

    def get_rect(self) -> pg.Rect:
        return self.rect

    def set_max_jump(self, n: int) -> None:
        self._max_jump = max(1, int(n))

    def get_max_jump(self) -> int:
        return self._max_jump

    def get_speed(self) -> int:
        return self._speed


class Enemy(pg.sprite.Sprite):
    """
    右端から左へ流れる敵（現時点はダミー表示）。

    注意:
    - HP/スコア/ボスなどの仕様は“追加機能”側に分離する前提でここには含めない
    - このクラスは「出現・移動・画面外で消滅」のみに責務を限定する
    """
    def __init__(self, stage: int):
        super().__init__()
        self._speed = stage_params(stage)["enemy_speed"]

        w = random.randint(40, 70)
        h = random.randint(40, 70)
        self.image = pg.Surface((w, h), pg.SRCALPHA)
        self.image.fill((230, 70, 70, 255))
        self.rect = self.image.get_rect()

        self.rect.left = WIDTH + random.randint(0, 160)
        self.rect.bottom = get_ground_y()

    def update(self) -> None:
        self.rect.x -= self._speed
        self.rect.bottom = get_ground_y()
        if self.rect.right < 0:
            self.kill()

    def get_rect(self) -> pg.Rect:
        return self.rect

    def get_speed(self) -> int:
        return self._speed
    
class Explosion(pg.sprite.Sprite):
    """
    爆発エフェクト：中心で拡大縮小を繰り返しながら消滅
    """
    def __init__(self, center_xy: tuple[int, int], life: int = 30):
        super().__init__()
        img = load_image("explosion.gif")
        self._imgs = [img, pg.transform.flip(img, True, True)] # 拡大縮小用に2枚用意
        self.image = self._imgs[0]
        self.rect = self.image.get_rect(center=center_xy)
        self._life = life

    def update(self) -> None:
        self._life -= 1
        self.image = self._imgs[(self._life // 5) % 2] # 5フレームごとに切替
        if self._life <= 0:
            self.kill()

class Beam(pg.sprite.Sprite):
    """
    攻撃弾（ビーム）。

    仕様:
    - 発射位置から右方向へ直進
    - 画面外に出たら消滅
    """
    def __init__(self, start_xy: tuple[int, int]):
        super().__init__()
        self.image = load_image("beam.png")
        self.rect = self.image.get_rect(center=start_xy)
        self._vx = 16

    def update(self) -> None:
        self.rect.x += self._vx
        if self.rect.left > WIDTH:
            self.kill()

class Arrow(pg.sprite.Sprite):
    """
    矢：放物線を描きつつ右へ進む
    """
    def __init__(self, start_xy: tuple[int, int]):
        super().__init__()
        self._base_image = load_image("arrow.png")  # 元画像を保持（回転はここから作る）
        self.image = self._base_image
        self.image = pg.transform.rotozoom(self._base_image, 0, 0.2)
        self.rect = self.image.get_rect(center=start_xy)

        self._vx = 14
        self._vy = -7.0
        self._g = 0.6

        self._angle = 0.0  # 現在角度（無駄な回転を減らす用）

    def update(self) -> None:
        """
        矢を更新する。
        - 右方向へ進みつつ重力で落下する
        - 上昇中(_vy<0)は右向き固定
        - 落下開始後(_vy>=0)は進行方向に合わせて右下向きに回転
        - 地面に触れた瞬間に消滅
        """
        # 位置更新
        self.rect.x += self._vx
        self._vy += self._g
        self.rect.y += int(self._vy)

        # --- 向き更新 ---
        # 発射直後（上昇中）は右向き固定、落ち始めたら進行方向に向ける
        if self._vy < 0:
            new_angle = 0.0
        else:
            # pygame座標はyが下に増えるので、角度は -atan2(vy, vx)
            new_angle = -math.degrees(math.atan2(self._vy, self._vx)) - 45

        # 角度が少し変わったときだけ回転（軽量化）
        if abs(new_angle - self._angle) > 1.0:
            self._angle = new_angle
            center = self.rect.center
            self.image = pg.transform.rotozoom(self._base_image, self._angle, 0.2)
            self.rect = self.image.get_rect(center=center)

        # 地面に触れた瞬間消滅
        if self.rect.bottom >= get_ground_y():
            self.kill()
        if self.rect.left > WIDTH:
            self.kill()


class ItemDef:
    """
    アイテムの“定義情報”を保持するクラス（スポーンや描画用）。

    Fields:
    - item_id: アイテム識別子（例: "Beam", "arrow", "kinoko", "tabaco"）
    - category: "attack" または "status"
    - img_file: 画像ファイル名
    - weight: 重み付き抽選で使う重み
    - scale: 描画倍率（Item生成時に適用）
    """
    def __init__(self, item_id: str, category: str, img_file: str, weight: int, scale: float = 1.0):
        self._item_id = item_id          # "Beam", "kinoko" など
        self._category = category        # "attack" or "status"
        self._img_file = img_file        # 画像ファイル名
        self._weight = weight
        self._scale = scale

    def get_item_id(self) -> str:
        return self._item_id

    def get_category(self) -> str:
        return self._category

    def get_img_file(self) -> str:
        return self._img_file
    
    def get_weight(self) -> int:
        return self._weight

    def get_scale(self) -> float:
        return self._scale

class Inventory:
    """
    プレイヤーの所持アイテム状態を管理する。

    仕様:
    - 攻撃アイテムは1つだけ保持（attackスロット）
    - 状態アイテムは1つだけ保持（statusスロット）
    - 同じ item_id を再取得すると、その item_id のレベルを +1
    - 同カテゴリで別 item_id を取得した場合は、後から取った方で置換する
      （レベルは PERSIST_ITEM_LEVEL の設定に従う）
    """
    def __init__(self, item_defs: dict[str, ItemDef]):
        self._defs = item_defs
        self._attack_id = None
        self._status_id = None
        self._levels = {}  # item_id -> level

    def pickup_attack(self, item_id: str) -> None:
        if self._attack_id == item_id:
            self._levels[item_id] = self._levels.get(item_id, 1) + 1
        else:
            self._attack_id = item_id
            if (not PERSIST_ITEM_LEVEL) or (item_id not in self._levels):
                self._levels[item_id] = 1

    def pickup_status_basic(self, item_id: str) -> None:
        # 特殊ルールは外で処理（tabaco/kinoko）
        if self._status_id == item_id:
            self._levels[item_id] = self._levels.get(item_id, 1) + 1
        else:
            self._status_id = item_id
            if (not PERSIST_ITEM_LEVEL) or (item_id not in self._levels):
                self._levels[item_id] = 1

    def clear_status(self) -> None:
        self._status_id = None

    def get_attack(self) -> tuple[str | None, int]:
        if self._attack_id is None:
            return None, 0
        return self._attack_id, self._levels.get(self._attack_id, 1)

    def get_status(self) -> tuple[str | None, int]:
        if self._status_id is None:
            return None, 0
        return self._status_id, self._levels.get(self._status_id, 1)
    
class Item(pg.sprite.Sprite):
    """
    画面右端から左へ流れるアイテム（取得対象）。

    - 画像は ItemDef に従って読み込む（scaleも適用）
    - 出現Xは右端外（WIDTH + 乱数）
    - 出現Yは「画面上限〜地面直上」の範囲でランダム
    - updateで左へ移動し、画面外に出たら消滅
    """
    def __init__(self, idef: ItemDef, stage: int):
        super().__init__()
        self._item_id = idef.get_item_id()
        self._category = idef.get_category()
        self._speed = stage_params(stage)["item_speed"]

        img = load_image(idef.get_img_file())
        if idef.get_scale() != 1.0:
            img = pg.transform.rotozoom(img, 0, idef.get_scale())
        self.image = img
        self.rect = self.image.get_rect()

        self.rect.left = WIDTH + random.randint(0, 200)

        # 地面より上のどこかに出す
        gy = get_ground_y()
        margin = 10
        lowest = gy - (self.rect.height // 2) - margin   # これより下に出さない
        highest = 60                                     # これより上に出さない（画面上部）

        highest = max(highest, self.rect.height // 2 + margin)
        self.rect.centery = random.randint(highest, lowest)

    def update(self) -> None:
        self.rect.x -= self._speed
        if self.rect.right < 0:
            self.kill()

    def get_item_id(self) -> str:
        return self._item_id

    def get_category(self) -> str:
        return self._category
    

# スポーン間隔(フレーム) と スポーン確率
ITEM_SPAWN_INTERVAL_STAGE1 = 90   # 1.5秒(60FPS想定)
ITEM_SPAWN_INTERVAL_STAGE2 = 70
ITEM_SPAWN_PROB_STAGE1 = 0.55
ITEM_SPAWN_PROB_STAGE2 = 0.65


def pick_weighted_item_id(item_defs: dict[str, ItemDef], stage: int) -> str:
    """
    item_defs の weight に基づいて item_id を1つ返す（重み付き抽選）。

    Args:
        item_defs: item_id -> ItemDef の辞書
        stage: 現状は抽選ロジックに影響しないが、将来ステージ別抽選に拡張できるため引数として保持

    Returns:
        str: 抽選された item_id
    """
    ids = list(item_defs.keys())
    weights = [max(0, item_defs[i].get_weight()) for i in ids]
    total = sum(weights)
    if total <= 0:
        # 全部0なら先頭
        return ids[0]

    r = random.randint(1, total)
    acc = 0
    for i, w in zip(ids, weights):
        acc += w
        if r <= acc:
            return i
    return ids[-1]


def maybe_spawn_item(tmr: int, stage: int, item_defs: dict[str, ItemDef], items: pg.sprite.Group) -> None:
    """
    アイテムをスポーンするかを判定し、スポーンする場合は items に追加する。

    - stage に応じてスポーン間隔(interval)と確率(prob)を切り替える
    - tmr が interval の倍数のタイミングのみ抽選する
    - 当選したら重み付き抽選で item_id を選ぶ
    """
    if stage == 1:
        interval = ITEM_SPAWN_INTERVAL_STAGE1
        prob = ITEM_SPAWN_PROB_STAGE1
    else:
        interval = ITEM_SPAWN_INTERVAL_STAGE2
        prob = ITEM_SPAWN_PROB_STAGE2

    if tmr % interval != 0:
        return

    if random.random() > prob:
        return

    item_id = pick_weighted_item_id(item_defs, stage)
    items.add(Item(item_defs[item_id], stage))
    
def apply_status_pickup(item_id: str, inv: Inventory, bird: Bird) -> None:
    """
    状態アイテム取得時の特殊ルールを適用する。

    仕様:
    - tabaco：所持中は二段ジャンプ不可（max_jump=1）
    - kinoko：
        - 無状態で取得 -> max_jump=3（状態は kinoko）
        - tabaco所持中に取得 -> tabacoを打ち消し無状態へ（max_jump=2）

    副作用:
    - inv の状態スロット（status）を書き換える
    - bird の最大ジャンプ回数を変更する
    """
    cur_status, cur_lv = inv.get_status()

    if item_id == "tabaco":
        inv.pickup_status_basic("tabaco")
        bird.set_max_jump(1)
        return

    if item_id == "kinoko":
        if cur_status == "tabaco":
            # 打ち消し：無状態へ戻す
            inv.clear_status()
            bird.set_max_jump(2)
            return
        else:
            inv.pickup_status_basic("kinoko")
            bird.set_max_jump(3)
            return

    # 他の状態アイテムが増えたらここに追加
    inv.pickup_status_basic(item_id)

def apply_status_from_current(inv: Inventory, bird: Bird) -> None:
    """
    ステージ切替などで、現在の所持状態に合わせてジャンプ数を再適用したい場合用
    """
    st, lv = inv.get_status()
    if st == "tabaco":
        bird.set_max_jump(1)
    elif st == "kinoko":
        bird.set_max_jump(3)
    else:
        bird.set_max_jump(2)


# =========================
# メイン
# =========================
def main():
    pg.display.set_caption("こうかとん横スクロール（ベース）")
    screen = pg.display.set_mode((WIDTH, HEIGHT))
    clock = pg.time.Clock()

    stage = 1
    params = stage_params(stage)

    bg = Background(params["bg_file"], params["bg_speed"])
    bird = Bird(3, (200, get_ground_y()))
    enemies = pg.sprite.Group()
    items = pg.sprite.Group()
    beams = pg.sprite.Group()
    arrows = pg.sprite.Group()
    exps = pg.sprite.Group()

    ITEM_DEFS = {
        # 攻撃
        "Beam":  ItemDef("Beam",  "attack", "beam.png",  weight=5, scale=1.0),
        "arrow":   ItemDef("arrow",   "attack", "arrow.png",   weight=3, scale=0.2),
        # 状態
        "kinoko": ItemDef("kinoko", "status", "kinoko.png", weight=4, scale=0.1),
        "tabaco": ItemDef("tabaco", "status", "tabaco.png", weight=2, scale=0.03),
    }
    inv = Inventory(ITEM_DEFS)

    # ===== 他の人のアイテムGroupを受け取る場所 =====
    # 統合するときは、次の1行を「相手が作った items（pg.sprite.Group）」に差し替えるだけでOK
    items = pg.sprite.Group()

    # ===== HP/Score/UI =====
    hp = HP_MAX
    score = 0
    font = pg.font.Font(None, 36)
    dmg_popup_tmr = 0
    inv_tmr = 0

    # ===== 右下UI（Attack/Status） =====
    current_attack: str | None = None
    current_status: str | None = None

    font_ui = pg.font.Font(None, 26)
    font_item = pg.font.Font(None, 22)

    attack_box = pg.Rect(
        WIDTH - (BOX_W * 2 + BOX_GAP) - BOX_MARGIN,
        HEIGHT - BOX_H - BOX_MARGIN,
        BOX_W, BOX_H
    )
    status_box = pg.Rect(
        WIDTH - BOX_W - BOX_MARGIN,
        HEIGHT - BOX_H - BOX_MARGIN,
        BOX_W, BOX_H
    )

    def read_item_info(it) -> tuple[str | None, str | None]:
        """
        他人実装の属性名ズレを吸収して (kind, name) を返す
        kind: "attack" or "status"
        name: 表示名
        """
        kind = None
        for k in ("kind", "type", "category"):
            v = getattr(it, k, None)
            if isinstance(v, str):
                kind = v.lower()
                break

        name = None
        for k in ("name", "item_name", "label"):
            v = getattr(it, k, None)
            if isinstance(v, str):
                name = v
                break

        if kind in ("atk", "attack_item"):
            kind = "attack"
        if kind in ("sts", "status_item"):
            kind = "status"

        return kind, name

    # ===== Score縁取り描画（追加）=====
    def draw_text_outline(surf: pg.Surface, text: str, font_: pg.font.Font, pos: tuple[int, int],
                          text_color: tuple[int, int, int], outline_color: tuple[int, int, int],
                          outline_px: int = 2) -> None:
        x, y = pos
        outline = font_.render(text, True, outline_color)
        for ox in range(-outline_px, outline_px + 1):
            for oy in range(-outline_px, outline_px + 1):
                if ox == 0 and oy == 0:
                    continue
                surf.blit(outline, (x + ox, y + oy))
        body = font_.render(text, True, text_color)
        surf.blit(body, (x, y))

    tmr = 0
    while True:
        key_lst = pg.key.get_pressed()

        for event in pg.event.get():
            if event.type == pg.QUIT:
                return 0
            if event.type == pg.KEYDOWN:
                if event.key == pg.K_ESCAPE:
                    return 0
                if event.key == pg.K_UP:
                    bird.try_jump()
                
                if event.key == pg.K_SPACE:
                    atk_id, atk_lv = inv.get_attack()
                    # 何も持ってなければ撃てない
                    if atk_id == "Beam":
                        beams.add(Beam((bird.get_rect().right + 30, bird.get_rect().centery)))
                    elif atk_id == "arrow":
                        arrows.add(Arrow((bird.get_rect().right + 30, bird.get_rect().centery)))

        # ステージ切替（全2ステージ）
        if stage == 1 and should_switch_stage(tmr):
            stage = 2
            params = stage_params(stage)
            bg = Background(params["bg_file"], params["bg_speed"])
            bird.get_rect().bottom = get_ground_y()
            apply_status_from_current(inv, bird)

        # 敵生成：複数流入（変更なし）
        if tmr % params["spawn_interval"] == 0:
            spawn_enemy(enemies, stage)
            if random.random() < 0.30:
                spawn_enemy(enemies, stage)

        maybe_spawn_item(tmr, stage, ITEM_DEFS, items)

        # ===== 描画（速度など変更なし）=====
        bg.update(screen)
        if DEBUG_DRAW_GROUND_LINE:
            pg.draw.line(screen, (0, 0, 0), (0, get_ground_y()), (WIDTH, get_ground_y()), 2)

        # 更新
        bird.update(key_lst, screen)
        enemies.update()
        items.update()
        beams.update()
        arrows.update()
        exps.update()

        hit1 = pg.sprite.groupcollide(enemies, beams, True, True) # ビーム当たり判定
        for emy in hit1.keys():
            exps.add(Explosion(emy.get_rect().center, life=30))

        hit2 = pg.sprite.groupcollide(enemies, arrows, True, True) # 矢当たり判定
        for emy in hit2.keys():
            exps.add(Explosion(emy.get_rect().center, life=30))
        picked = pg.sprite.spritecollide(bird, items, True) # アイテム取得判定
        for it in picked:
            item_id = it.get_item_id()
            cat = ITEM_DEFS[item_id].get_category()

            if cat == "attack": # 攻撃アイテム
                inv.pickup_attack(item_id)
            else:
                apply_status_pickup(item_id, inv, bird)
        
        items.update()  # ← 他の人のアイテム（右→左）は相手update内で動く想定

        # ===== 敵ダメージ（HP-20）=====
        if inv_tmr > 0:
            inv_tmr -= 1

        hit_list = pg.sprite.spritecollide(bird, enemies, False)
        if hit_list and inv_tmr == 0:
            hp = max(0, hp - DMG)

            if hp <= 0:
                return 0

            for e in hit_list:
                e.kill()

            dmg_popup_tmr = POPUP_FRAMES
            inv_tmr = INV_FRAMES

        # ===== アイテム取得（触れたら取得→右下表示を置き換え）=====
        got_items = pg.sprite.spritecollide(bird, items, True)  # True=拾ったら消える
        for it in got_items:
            kind, name = read_item_info(it)
            if kind == "attack":
                current_attack = name if name is not None else "Unknown"
            elif kind == "status":
                current_status = name if name is not None else "Unknown"

        # 描画（スプライト）
        items.draw(screen)
        enemies.draw(screen)
        items.draw(screen)
        beams.draw(screen)
        arrows.draw(screen)
        exps.draw(screen)

        # ===== UI：HP（左下）=====
        hp_pos = (20, HEIGHT - 50)
        hp_text = font.render(f"HP:{hp}", True, (255, 255, 255))
        screen.blit(hp_text, hp_pos)

        # HPバー（残りHPを緑）
        bar_x, bar_y = 20, HEIGHT - 25
        bar_w, bar_h = 200, 14
        pg.draw.rect(screen, (0, 0, 0), (bar_x - 2, bar_y - 2, bar_w + 4, bar_h + 4))
        pg.draw.rect(screen, (255, 255, 255), (bar_x, bar_y, bar_w, bar_h))
        hp_ratio = max(0, min(1, hp / HP_MAX))
        pg.draw.rect(screen, (0, 200, 0), (bar_x, bar_y, int(bar_w * hp_ratio), bar_h))

        # 「-20」赤表示（約2秒）
        if dmg_popup_tmr > 0:
            dmg_popup_tmr -= 1
            dmg_text = font.render(f"-{DMG}", True, (255, 0, 0))
            screen.blit(dmg_text, (hp_pos[0] + hp_text.get_width() + 10, hp_pos[1]))

        # ===== UI：Score（右上：白縁＋中黒）=====
        score_str = f"Score:{score}"
        tmp = font.render(score_str, True, (0, 0, 0))  # 幅取得用
        score_pos = (WIDTH - tmp.get_width() - 20, 20)
        draw_text_outline(screen, score_str, font, score_pos, (0, 0, 0), (255, 255, 255), outline_px=2)

        # ===== UI：右下 Attack / Status（黒塗り＋白枠）=====
        pg.draw.rect(screen, (0, 0, 0), attack_box)
        pg.draw.rect(screen, (255, 255, 255), attack_box, 2)
        pg.draw.rect(screen, (0, 0, 0), status_box)
        pg.draw.rect(screen, (255, 255, 255), status_box, 2)

        atk_label = font_ui.render("Attack", True, (255, 255, 255))
        sta_label = font_ui.render("Status", True, (255, 255, 255))
        screen.blit(atk_label, (attack_box.x + 10, attack_box.y + 8))
        screen.blit(sta_label, (status_box.x + 10, status_box.y + 8))

        atk_name = current_attack if current_attack is not None else "-"
        sta_name = current_status if current_status is not None else "-"
        atk_text = font_item.render(atk_name, True, (255, 255, 255))
        sta_text = font_item.render(sta_name, True, (255, 255, 255))
        screen.blit(atk_text, (attack_box.x + 12, attack_box.y + 40))
        screen.blit(sta_text, (status_box.x + 12, status_box.y + 40))

        pg.display.update()
        
        tmr += 1
        clock.tick(FPS)


if __name__ == "__main__":
    pg.init()
    main()
    pg.quit()
    sys.exit()
