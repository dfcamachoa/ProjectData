# Running on Windows

Spark itself runs fine on a Windows PC, but **how** you run it matters. There are
two paths; the WSL path is strongly recommended and is the one verified working.

## Recommended: WSL (Ubuntu) — verified working

Running under WSL avoids the Windows/Hadoop `winutils.exe` requirement entirely and
matches the Linux environment Spark runs on in production.

```bash
wsl                                     # from PowerShell, drop into Ubuntu
# copy the project onto the Linux filesystem (NOT /mnt/c — see note below)
cp -r /mnt/c/dev/bronze_ingestion ~/bronze_ingestion
cd ~/bronze_ingestion

sudo apt update && sudo apt install -y openjdk-17-jdk python3-venv
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip && pip install pyspark==3.5.1 delta-spark==3.2.0

python run_tests.py        # pure-core unit tests (no Spark)
python smoke_local.py      # real Spark + Delta end-to-end + idempotency check
```

Expected `smoke_local.py` outcome (verified 2026-08-28 on WSL Ubuntu, Python 3.8):

- First ingest: 4 files, 4 inserted.
- Second ingest: 0 inserted, 4 skipped (idempotent).
- Format detection: DEXPI/`ORIGINATING_SYSTEM`, POSTPROC/`ORIGINATING_SYSTEM`,
  POSTPROC/`SEGMENT_TAGNAME` (the no-originator fallback), and the malformed file
  landed with `header_parse_ok = false` (flagged, not rejected).
- "Idempotency + uniqueness checks passed."

### Two WSL gotchas that matter

1. **Keep the project and the Delta table on the Linux filesystem** (`~/...`), not on
   the Windows mount (`/mnt/c/...`). The Windows drive mount under WSL is slow and its
   file-locking upsets Delta's transaction log.
2. **Java must be 8, 11, or 17 — not 21.** Spark 3.5 does not support Java 21
   (that needs Spark 4.0). `openjdk-17-jdk` as above is correct. If Spark can't find
   Java, set `export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64` in `~/.bashrc`.

## Alternative: native Windows (needs winutils)

Native Windows Spark fails at startup with
`HADOOP_HOME and hadoop.home.dir are unset` until you supply Hadoop's Windows
helper binaries:

1. Get `winutils.exe` **and** `hadoop.dll` for **Hadoop 3.3.x** (Spark 3.5 bundles
   Hadoop 3.3.x) from a maintained community repo (`cdarlint/winutils` or
   `kontext-tech/winutils` on GitHub — the `hadoop-3.3.x/bin` folder).
2. Place them at `C:\hadoop\bin\winutils.exe` and `C:\hadoop\bin\hadoop.dll`.
3. Set the environment variable and PATH, then open a **fresh** terminal:
   ```powershell
   setx HADOOP_HOME "C:\hadoop"
   # add C:\hadoop\bin to PATH; if hadoop.dll still isn't found,
   # copy it into C:\Windows\System32
   ```

Native Windows can still hit follow-on issues (Python-worker paths, temp-dir
permissions) that WSL does not — which is why WSL is the recommended path.

## Note

`python run_tests.py` (the pure-core tests) works on native Windows too — that path
never touches Spark, so it needs neither winutils nor WSL.
