# Backend API

## Database lokal

Backend menggunakan SQLAlchemy async dan PostgreSQL. Runtime memakai `DATABASE_URL`
(pooled URL untuk Neon); Alembic memakai `DATABASE_DIRECT_URL` (direct URL). Psycopg 3 async
dipakai agar parameter `sslmode` dan `channel_binding` dari Neon diterapkan. File `.env` lokal
dibaca saat perintah dijalankan dari folder `backend/`; jangan menyalin URL atau key kredensial
ke repo atau log. File `.env.example` berisi placeholder saja.

Contoh setup pada PowerShell, setelah database dev kosong dibuat:

```powershell
uv sync --all-groups
$env:APP_ENV = "development"
$env:DATABASE_DIRECT_URL = "postgresql+psycopg://USER:PASSWORD@localhost:5432/portfolio_dev"
uv run alembic upgrade head
$env:DATABASE_URL = $env:DATABASE_DIRECT_URL
$env:PUBLIC_SITE_ORIGIN = "http://localhost:4321"
uv run uvicorn app.main:app --reload
```

Dokumentasi OpenAPI tersedia di `http://localhost:8000/api/docs`; route publik dan contoh response
ada di [public-api-reference.md](../docs/portfolio-terminal/public-api-reference.md). CORS membaca
`ALLOWED_ORIGINS` sebagai allowlist exact, menolak wildcard, dan tidak menggunakan credentials.

Untuk menghasilkan SQL tanpa koneksi, set `DATABASE_DIRECT_URL` ke URL Postgres lalu jalankan:

```powershell
uv run alembic upgrade head --sql
```

Alembic menolak `APP_ENV=production`. Migrasi production belum disiapkan sebagai release
workflow; jangan mengatasi guard tersebut dengan menjalankan skrip ini terhadap data nyata.
Migration awal dapat di-downgrade pada lingkungan kosong untuk keperluan pengujian; downgrade
menghapus seluruh tabel portofolio dan tidak ditujukan sebagai prosedur rollback data production.

## Pemeriksaan lokal

```powershell
uv run ruff check app migrations tests
uv run mypy
uv run pytest
uv lock --check
```

Tes otomatis memakai SQLite in-memory untuk constraint dan perilaku transaksi yang portable.
Penerimaan PostgreSQL-specific (JSONB, partial index, timezone round-trip, dan operasi migration
di Postgres) tetap perlu dijalankan terhadap database development PostgreSQL kosong.

Fase 05 menyediakan autentikasi admin, session tersimpan sebagai hash HMAC, pembatasan login berbasis PostgreSQL, dan bootstrap interaktif melalui `uv run python -m app.scripts.bootstrap_admin`. Fase 06 menambahkan CRUD konten; fase 07 menambahkan upload media S3 privat, normalisasi gambar, urutan galeri dan delivery yang memeriksa publikasi. Untuk mencoba upload secara lokal, isi `AWS_ENDPOINT_URL_S3`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, dan `AWS_S3_BUCKET` dengan bucket nonproduksi yang telah diverifikasi private; set `AWS_S3_PRIVATE_BUCKET_CONFIRMED=true` hanya setelah verifikasi itu dan izinkan CORS hanya dari origin frontend lokal untuk metode `POST`. Uji integrasi Neon masih diblokir hingga bucket/branch nonproduksi diverifikasi. Kontrak endpoint ada di [admin-api-reference.md](../docs/portfolio-terminal/admin-api-reference.md). Jangan jalankan bootstrap, upload, atau migrasi pada Neon sebelum target branch nonproduksi dikonfirmasi.

Auth membutuhkan `SESSION_HASH_KEY`, `LOGIN_RATE_KEY`, `AUTH_PROXY_KEY`, dan `PUBLIC_SITE_ORIGIN`; contoh nilai ada di `.env.example`. `AUTH_PROXY_KEY` harus sama pada backend dan frontend BFF. Jangan menampilkan atau meng-commit nilai `.env`.
