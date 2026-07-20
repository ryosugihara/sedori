# -*- coding: utf-8 -*-
"""
相場DBをGitHub Releaseに『分割して』保存/取得する部品。
大きいDBを45MBずつに分けて保存し、最後にmanifestを更新する。途中で失敗しても
前の完全なDBが残る(消えない)。使い方: python db_release.py download / upload
"""
import os
import sys
import json
import glob
import time
import subprocess

TAG = os.environ.get("SOUBA_DB_TAG", "souba-db")
DB = "せどり/データ/data/souba_db.sqlite"
GZ = DB + ".gz"
CHUNK_MB = 45
MANIFEST = "souba_db_manifest.json"
LEGACY = "souba_db.sqlite.gz"
REPO = os.environ.get("GITHUB_REPOSITORY", "ryosugihara/sedori")
WORK = "/tmp/dbrel"


def gh(*args, check=False):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=check)


def _assets():
    r = gh("release", "view", TAG, "--repo", REPO, "--json", "assets")
    if r.returncode != 0:
        return []
    try:
        return [a["name"] for a in json.loads(r.stdout).get("assets", [])]
    except Exception:
        return []


def download():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    r = gh("release", "download", TAG, "--repo", REPO, "--pattern", MANIFEST,
           "--dir", WORK, "--clobber")
    mpath = os.path.join(WORK, MANIFEST)
    if r.returncode == 0 and os.path.exists(mpath):
        parts = json.load(open(mpath)).get("parts", [])
        ok = bool(parts)
        for p in parts:
            rr = gh("release", "download", TAG, "--repo", REPO, "--pattern", p,
                    "--dir", WORK, "--clobber")
            if rr.returncode != 0 or not os.path.exists(os.path.join(WORK, p)):
                ok = False
                break
        if ok:
            with open(GZ, "wb") as out:
                for p in parts:
                    with open(os.path.join(WORK, p), "rb") as f:
                        out.write(f.read())
            subprocess.run(["gunzip", "-f", GZ], check=True)
            print("分割DBを取得・結合しました（%d個）" % len(parts))
            return True
        print("分割DBのパートが欠けています")
    r = gh("release", "download", TAG, "--repo", REPO, "--pattern", LEGACY,
           "--dir", os.path.dirname(DB), "--clobber")
    if r.returncode == 0 and os.path.exists(GZ):
        subprocess.run(["gunzip", "-f", GZ], check=True)
        print("単一DB（旧形式）を取得しました")
        return True
    print("相場DBが見つかりません")
    return False


def upload():
    if not os.path.exists(DB) or os.path.getsize(DB) == 0:
        print("DBが無い/空のためアップロード中止（既存を守る）")
        return False
    os.makedirs(WORK, exist_ok=True)
    old_parts = []
    r = gh("release", "download", TAG, "--repo", REPO, "--pattern", MANIFEST,
           "--dir", WORK, "--clobber")
    mpath = os.path.join(WORK, MANIFEST)
    if r.returncode == 0 and os.path.exists(mpath):
        try:
            old_parts = json.load(open(mpath)).get("parts", [])
        except Exception:
            old_parts = []
    subprocess.run('gzip -f -c "%s" > "%s"' % (DB, GZ), shell=True, check=True)
    for f in glob.glob(os.path.join(WORK, "part_*")):
        os.remove(f)
    subprocess.run(["split", "-b", "%dm" % CHUNK_MB, "-d", "-a", "3", GZ,
                    os.path.join(WORK, "part_")], check=True)
    locals_ = sorted(glob.glob(os.path.join(WORK, "part_*")))
    gen = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
    part_names = []
    for i, pl in enumerate(locals_):
        name = "souba_db.%s.gz.part%03d" % (gen, i)
        dst = os.path.join(WORK, name)
        os.replace(pl, dst)
        uploaded = False
        for t in range(3):
            if gh("release", "upload", TAG, dst, "--repo", REPO, "--clobber").returncode == 0:
                uploaded = True
                break
            print("パート%s 失敗、再試行(%d/3)" % (name, t + 1))
            time.sleep(10)
        if not uploaded:
            print("パート%s アップロード失敗。中止（manifest未更新なので旧DBは無事）" % name)
            return False
        part_names.append(name)
    man = {"gen": gen, "parts": part_names, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    open(mpath, "w").write(json.dumps(man, ensure_ascii=False))
    gh("release", "upload", TAG, mpath, "--repo", REPO, "--clobber", check=True)
    print("分割アップロード完了（%d個）" % len(part_names))
    for name in set(old_parts) - set(part_names):
        gh("release", "delete-asset", TAG, name, "--repo", REPO, "--yes")
    if LEGACY in _assets():
        gh("release", "delete-asset", TAG, LEGACY, "--repo", REPO, "--yes")
    return True


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "download"
    sys.exit(0 if (upload() if action == "upload" else download()) else 1)
