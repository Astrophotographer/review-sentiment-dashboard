"""
데이터베이스(SQLite) 접근 모듈
------------------------------
raw_reviews  : 파일에서 그대로 읽어들인 원본 리뷰
clean_reviews: 정제(clean) 과정을 거친 리뷰 + AI 감정분석 결과 컬럼 포함
extractions  : extract 커맨드로 생성된 키워드/요약 결과 저장

영구 저장소로 SQLite 파일을 사용하며, 메모리(List/Dict)만으로 데이터를 다루지 않는다.
"""
import sqlite3
import os
from typing import Optional, List, Dict, Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_text TEXT NOT NULL,
    rating INTEGER,
    review_date TEXT,
    product TEXT,
    category TEXT,
    source_file TEXT,
    imported_at TEXT NOT NULL,
    dedup_hash TEXT,
    is_cleaned INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_dedup_hash ON raw_reviews (dedup_hash);

CREATE TABLE IF NOT EXISTS clean_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_id INTEGER,
    review_text TEXT NOT NULL,
    rating INTEGER,
    review_date TEXT,
    product TEXT,
    category TEXT,
    language TEXT,
    dedup_hash TEXT,
    cleaned_at TEXT NOT NULL,
    sentiment TEXT,
    confidence REAL,
    analyzed_at TEXT,
    aspect_json TEXT,
    FOREIGN KEY (raw_id) REFERENCES raw_reviews (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_clean_dedup_hash ON clean_reviews (dedup_hash);

CREATE TABLE IF NOT EXISTS extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    extraction_type TEXT NOT NULL,
    condition_desc TEXT,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    review_count INTEGER NOT NULL DEFAULT 0,
    analyzed_count INTEGER NOT NULL DEFAULT 0,
    temp_c REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS model_run_results (
    run_id INTEGER NOT NULL,
    review_id INTEGER NOT NULL,
    sentiment TEXT,
    confidence REAL,
    PRIMARY KEY (run_id, review_id),
    FOREIGN KEY (run_id) REFERENCES model_runs (id),
    FOREIGN KEY (review_id) REFERENCES clean_reviews (id)
);
"""


class Database:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(clean_reviews)").fetchall()}
        if "aspect_json" not in cols:
            self.conn.execute("ALTER TABLE clean_reviews ADD COLUMN aspect_json TEXT")

    # ---------------- raw_reviews ----------------
    def raw_hash_exists(self, dedup_hash: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM raw_reviews WHERE dedup_hash = ? LIMIT 1", (dedup_hash,))
        return cur.fetchone() is not None

    def get_raw_by_hash(self, dedup_hash: str) -> Optional[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM raw_reviews WHERE dedup_hash = ? LIMIT 1", (dedup_hash,))
        return cur.fetchone()

    def insert_raw(self, review_text, rating, review_date, product, source_file, imported_at, dedup_hash, category=None) -> int:
        cur = self.conn.execute(
            """INSERT INTO raw_reviews
               (review_text, rating, review_date, product, category, source_file, imported_at, dedup_hash, is_cleaned)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (review_text, rating, review_date, product, category, source_file, imported_at, dedup_hash),
        )
        self.conn.commit()
        return cur.lastrowid

    def upsert_raw(self, review_text, rating, review_date, product, source_file, imported_at, dedup_hash, category=None):
        """dedup_hash 가 이미 존재하면 기존 행을 갱신(재정제 대상으로 되돌림)하고, 없으면 새로 삽입한다."""
        existing = self.get_raw_by_hash(dedup_hash)
        if existing:
            self.conn.execute(
                """UPDATE raw_reviews SET review_text=?, rating=?, review_date=?, product=?, category=?,
                   source_file=?, imported_at=?, is_cleaned=0 WHERE dedup_hash=?""",
                (review_text, rating, review_date, product, category, source_file, imported_at, dedup_hash),
            )
            self.conn.commit()
            return existing["id"], "updated"
        new_id = self.insert_raw(review_text, rating, review_date, product, source_file, imported_at, dedup_hash, category)
        return new_id, "inserted"

    def get_uncleaned_raw(self) -> List[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM raw_reviews WHERE is_cleaned = 0 ORDER BY id")
        return cur.fetchall()

    def mark_raw_cleaned(self, raw_id: int):
        self.conn.execute("UPDATE raw_reviews SET is_cleaned = 1 WHERE id = ?", (raw_id,))
        self.conn.commit()

    def count_raw(self) -> int:
        return self.conn.execute("SELECT COUNT(*) c FROM raw_reviews").fetchone()["c"]

    # ---------------- clean_reviews ----------------
    def clean_hash_exists(self, dedup_hash: str) -> Optional[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM clean_reviews WHERE dedup_hash = ? LIMIT 1", (dedup_hash,))
        return cur.fetchone()

    def insert_clean(self, raw_id, review_text, rating, review_date, product, language, dedup_hash, cleaned_at, category=None) -> int:
        cur = self.conn.execute(
            """INSERT INTO clean_reviews
               (raw_id, review_text, rating, review_date, product, category, language, dedup_hash, cleaned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (raw_id, review_text, rating, review_date, product, category, language, dedup_hash, cleaned_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def upsert_clean(self, raw_id, review_text, rating, review_date, product, language, dedup_hash, cleaned_at, category=None):
        existing = self.clean_hash_exists(dedup_hash)
        if existing:
            self.conn.execute(
                """UPDATE clean_reviews SET raw_id=?, review_text=?, rating=?, review_date=?,
                   product=?, category=?, language=?, cleaned_at=? WHERE dedup_hash=?""",
                (raw_id, review_text, rating, review_date, product, category, language, cleaned_at, dedup_hash),
            )
            self.conn.commit()
            return existing["id"], "updated"
        else:
            new_id = self.insert_clean(raw_id, review_text, rating, review_date, product, language, dedup_hash, cleaned_at, category)
            return new_id, "inserted"

    def get_clean_by_id(self, review_id: int) -> Optional[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM clean_reviews WHERE id = ?", (review_id,))
        return cur.fetchone()

    def get_unanalyzed(self, limit: Optional[int] = None) -> List[sqlite3.Row]:
        sql = "SELECT * FROM clean_reviews WHERE sentiment IS NULL ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.conn.execute(sql).fetchall()

    def get_all_clean(self, limit: Optional[int] = None) -> List[sqlite3.Row]:
        sql = "SELECT * FROM clean_reviews ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.conn.execute(sql).fetchall()

    def get_clean_by_ids(self, ids: List[int]) -> List[sqlite3.Row]:
        if not ids:
            return []
        q = ",".join("?" * len(ids))
        return self.conn.execute(f"SELECT * FROM clean_reviews WHERE id IN ({q})", ids).fetchall()

    def update_analysis(
        self,
        review_id: int,
        sentiment: str,
        confidence: float,
        analyzed_at: str,
        aspect_json: Optional[str] = None,
    ):
        if aspect_json is None:
            self.conn.execute(
                "UPDATE clean_reviews SET sentiment=?, confidence=?, analyzed_at=? WHERE id=?",
                (sentiment, confidence, analyzed_at, review_id),
            )
        else:
            self.conn.execute(
                "UPDATE clean_reviews SET sentiment=?, confidence=?, analyzed_at=?, aspect_json=? WHERE id=?",
                (sentiment, confidence, analyzed_at, aspect_json, review_id),
            )
        self.conn.commit()

    def update_aspects_only(self, review_id: int, aspect_json: str):
        self.conn.execute(
            "UPDATE clean_reviews SET aspect_json=? WHERE id=?",
            (aspect_json, review_id),
        )
        self.conn.commit()

    def query_clean(
        self,
        sentiment: Optional[str] = None,
        rating: Optional[int] = None,
        rating_min: Optional[int] = None,
        rating_max: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        product: Optional[str] = None,
        category: Optional[str] = None,
        language: Optional[str] = None,
        sort_by: str = "id",
        sort_dir: str = "asc",
        page: int = 1,
        page_size: int = 10,
    ) -> Dict[str, Any]:
        clauses, params = [], []
        if sentiment and sentiment != "all":
            clauses.append("sentiment = ?")
            params.append(sentiment)
        if rating:
            clauses.append("rating = ?")
            params.append(rating)
        if rating_min:
            clauses.append("rating >= ?")
            params.append(rating_min)
        if rating_max:
            clauses.append("rating <= ?")
            params.append(rating_max)
        if date_from:
            clauses.append("review_date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("review_date <= ?")
            params.append(date_to)
        if product:
            clauses.append("product = ?")
            params.append(product)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if language:
            clauses.append("language = ?")
            params.append(language)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        sort_col = sort_by if sort_by in ("id", "rating", "review_date", "sentiment", "confidence") else "id"
        sort_dir = "DESC" if str(sort_dir).lower() == "desc" else "ASC"

        total = self.conn.execute(f"SELECT COUNT(*) c FROM clean_reviews {where}", params).fetchone()["c"]
        offset = (max(page, 1) - 1) * page_size
        rows = self.conn.execute(
            f"SELECT * FROM clean_reviews {where} ORDER BY {sort_col} {sort_dir} LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        return {"rows": rows, "total": total, "page": page, "page_size": page_size}

    def search_reviews(self, keyword: str, page: int = 1, page_size: int = 10) -> Dict[str, Any]:
        """[사용자 편의] 리뷰 원문에서 키워드를 검색한다 (list의 구조적 필터와 달리
        자유 텍스트 검색). 제품명에도 매칭되면 함께 찾아준다."""
        like = f"%{keyword}%"
        total = self.conn.execute(
            "SELECT COUNT(*) c FROM clean_reviews WHERE review_text LIKE ? OR product LIKE ?",
            (like, like),
        ).fetchone()["c"]
        offset = (max(page, 1) - 1) * page_size
        rows = self.conn.execute(
            "SELECT * FROM clean_reviews WHERE review_text LIKE ? OR product LIKE ? "
            "ORDER BY id LIMIT ? OFFSET ?",
            (like, like, page_size, offset),
        ).fetchall()
        return {"rows": rows, "total": total, "page": page, "page_size": page_size}

    def get_stats(self) -> Dict[str, Any]:
        total = self.conn.execute("SELECT COUNT(*) c FROM clean_reviews").fetchone()["c"]
        analyzed = self.conn.execute("SELECT COUNT(*) c FROM clean_reviews WHERE sentiment IS NOT NULL").fetchone()["c"]
        sentiment_rows = self.conn.execute(
            "SELECT sentiment, COUNT(*) c FROM clean_reviews WHERE sentiment IS NOT NULL GROUP BY sentiment"
        ).fetchall()
        rating_rows = self.conn.execute(
            "SELECT rating, COUNT(*) c FROM clean_reviews WHERE rating IS NOT NULL GROUP BY rating ORDER BY rating DESC"
        ).fetchall()
        avg_rating = self.conn.execute("SELECT AVG(rating) a FROM clean_reviews WHERE rating IS NOT NULL").fetchone()["a"]
        avg_confidence = self.conn.execute(
            "SELECT AVG(confidence) a FROM clean_reviews WHERE confidence IS NOT NULL"
        ).fetchone()["a"]
        language_rows = self.conn.execute(
            "SELECT language, COUNT(*) c FROM clean_reviews WHERE language IS NOT NULL GROUP BY language"
        ).fetchall()
        return {
            "total": total,
            "analyzed": analyzed,
            "sentiment_dist": {r["sentiment"]: r["c"] for r in sentiment_rows},
            "rating_dist": {r["rating"]: r["c"] for r in rating_rows},
            "avg_rating": avg_rating,
            "avg_confidence": avg_confidence,
            "language_dist": {r["language"]: r["c"] for r in language_rows},
        }

    def get_products(self) -> List[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT product FROM clean_reviews WHERE product IS NOT NULL AND product != ''"
        ).fetchall()
        return [r["product"] for r in rows]

    def get_categories(self) -> List[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT category FROM clean_reviews WHERE category IS NOT NULL AND category != ''"
        ).fetchall()
        return [r["category"] for r in rows]

    def get_language_dist(self) -> Dict[str, int]:
        rows = self.conn.execute(
            "SELECT language, COUNT(*) c FROM clean_reviews WHERE language IS NOT NULL GROUP BY language"
        ).fetchall()
        return {r["language"]: r["c"] for r in rows}

    # ---------------- extractions ----------------
    def insert_extraction(self, extraction_type: str, condition_desc: str, result_json: str, created_at: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO extractions (extraction_type, condition_desc, result_json, created_at) VALUES (?, ?, ?, ?)",
            (extraction_type, condition_desc, result_json, created_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_latest_extraction(self, extraction_type: Optional[str] = None) -> Optional[sqlite3.Row]:
        if extraction_type:
            cur = self.conn.execute(
                "SELECT * FROM extractions WHERE extraction_type=? ORDER BY id DESC LIMIT 1", (extraction_type,)
            )
        else:
            cur = self.conn.execute("SELECT * FROM extractions ORDER BY id DESC LIMIT 1")
        return cur.fetchone()

    # ---------------- model_runs (채점 스냅샷) ----------------
    def count_model_runs(self) -> int:
        return self.conn.execute("SELECT COUNT(*) c FROM model_runs").fetchone()["c"]

    def save_model_run(
        self,
        provider: str,
        model: str,
        label: str,
        created_at: str,
        temp_c: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> int:
        """현재 clean_reviews 감정 결과를 스냅샷으로 저장한다."""
        rows = self.get_all_clean()
        analyzed = sum(1 for r in rows if r["sentiment"])
        cur = self.conn.execute(
            """INSERT INTO model_runs
               (provider, model, label, created_at, review_count, analyzed_count, temp_c, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (provider, model, label, created_at, len(rows), analyzed, temp_c, notes),
        )
        run_id = cur.lastrowid
        self.conn.executemany(
            """INSERT INTO model_run_results (run_id, review_id, sentiment, confidence)
               VALUES (?, ?, ?, ?)""",
            [(run_id, r["id"], r["sentiment"], r["confidence"]) for r in rows],
        )
        self.conn.commit()
        return run_id

    def seed_model_run_if_empty(self, provider: str, model: str, label: str, created_at: str) -> Optional[int]:
        """model_runs 가 비어 있고 분석된 clean 리뷰가 있으면 시드 스냅샷 1개를 만든다."""
        if self.count_model_runs() > 0:
            return None
        analyzed = self.conn.execute(
            "SELECT COUNT(*) c FROM clean_reviews WHERE sentiment IS NOT NULL"
        ).fetchone()["c"]
        if analyzed <= 0:
            return None
        return self.save_model_run(provider, model, label, created_at, temp_c=None, notes="기존 분석 결과 시드")

    def list_model_runs(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM model_runs ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_model_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM model_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def compare_model_runs(self, run_a: int, run_b: int, disagreement_limit: int = 50) -> Dict[str, Any]:
        meta_a = self.get_model_run(run_a)
        meta_b = self.get_model_run(run_b)
        if not meta_a or not meta_b:
            raise ValueError("존재하지 않는 런 ID 입니다.")

        rows_a = self.conn.execute(
            "SELECT review_id, sentiment, confidence FROM model_run_results WHERE run_id=?",
            (run_a,),
        ).fetchall()
        rows_b = self.conn.execute(
            "SELECT review_id, sentiment, confidence FROM model_run_results WHERE run_id=?",
            (run_b,),
        ).fetchall()
        map_a = {r["review_id"]: r for r in rows_a}
        map_b = {r["review_id"]: r for r in rows_b}
        common_ids = sorted(set(map_a) & set(map_b))

        def dist(mmap):
            d = {"positive": 0, "neutral": 0, "negative": 0}
            confs = []
            for rid in common_ids:
                s = mmap[rid]["sentiment"]
                if s in d:
                    d[s] += 1
                if mmap[rid]["confidence"] is not None and s:
                    confs.append(float(mmap[rid]["confidence"]))
            total = sum(d.values()) or 1
            return {
                "counts": d,
                "ratios": {k: round(v / total * 100, 1) for k, v in d.items()},
                "avg_confidence": round(sum(confs) / len(confs), 3) if confs else None,
            }

        compared = 0
        agree = 0
        disagreements = []
        for rid in common_ids:
            sa = map_a[rid]["sentiment"]
            sb = map_b[rid]["sentiment"]
            if sa is None and sb is None:
                continue
            compared += 1
            if sa == sb:
                agree += 1
                continue
            ca = map_a[rid]["confidence"]
            cb = map_b[rid]["confidence"]
            ca_f = float(ca) if ca is not None else 0.0
            cb_f = float(cb) if cb is not None else 0.0
            disagreements.append({
                "review_id": rid,
                "sentiment_a": sa,
                "confidence_a": ca,
                "sentiment_b": sb,
                "confidence_b": cb,
                "conf_delta": abs(ca_f - cb_f),
            })

        disagreements.sort(key=lambda x: -x["conf_delta"])
        # 리뷰 본문/제품 보강
        top = disagreements[:disagreement_limit]
        if top:
            ids = [d["review_id"] for d in top]
            q = ",".join("?" * len(ids))
            info = {
                r["id"]: r
                for r in self.conn.execute(
                    f"SELECT id, product, review_text FROM clean_reviews WHERE id IN ({q})", ids
                ).fetchall()
            }
            for d in top:
                row = info.get(d["review_id"])
                if row:
                    text = row["review_text"] or ""
                    d["product"] = row["product"]
                    d["review_excerpt"] = text if len(text) <= 80 else text[:77] + "..."

        return {
            "run_a": meta_a,
            "run_b": meta_b,
            "common_review_count": len(common_ids),
            "compared_count": compared,
            "agree_count": agree,
            "agreement_rate": round(agree / compared * 100, 1) if compared else None,
            "dist_a": dist(map_a),
            "dist_b": dist(map_b),
            "disagreement_total": len(disagreements),
            "disagreements": top,
        }

    def close(self):
        self.conn.close()
