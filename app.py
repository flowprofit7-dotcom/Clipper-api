from flask import Flask, request, jsonify, send_file
import subprocess, os, uuid, threading, logging, sys

# Setup logging supaya muncul di Railway
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
JOBS = {}
OUTPUT_DIR = "/tmp/clips"
COOKIES_FILE = "/tmp/cookies.txt"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Tulis cookies dari environment variable
cookies_content = os.environ.get("YOUTUBE_COOKIES", "")
if cookies_content:
    with open(COOKIES_FILE, "w") as f:
        f.write(cookies_content)
    logger.info("✅ Cookies berhasil ditulis ke file")
else:
    logger.warning("⚠️ YOUTUBE_COOKIES tidak ditemukan di environment")

def process_video(job_id, youtube_url, clips):
    logger.info(f"🚀 Mulai proses job {job_id}")
    logger.info(f"📺 URL: {youtube_url}")
    logger.info(f"✂️ Jumlah clips: {len(clips)}")

    try:
        JOBS[job_id]["status"] = "downloading"
        raw_path = f"{OUTPUT_DIR}/{job_id}_raw.mp4"

        cmd = [
            "yt-dlp",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "--geo-bypass",
            "--no-check-certificates",
            "--add-header", "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "--add-header", "Accept-Language:en-US,en;q=0.9",
        ]

        if os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 0:
            cmd.extend(["--cookies", COOKIES_FILE])
            logger.info("🍪 Menggunakan cookies")

        cmd.extend(["-o", raw_path, youtube_url])

        logger.info(f"⬇️ Menjalankan yt-dlp...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        logger.info(f"yt-dlp stdout: {result.stdout[-500:] if result.stdout else 'kosong'}")
        logger.info(f"yt-dlp stderr: {result.stderr[-500:] if result.stderr else 'kosong'}")
        logger.info(f"yt-dlp return code: {result.returncode}")

        if result.returncode != 0:
            raise Exception(f"yt-dlp error: {result.stderr[-300:]}")

        logger.info("✅ Download selesai, mulai clipping...")
        JOBS[job_id]["status"] = "clipping"
        result_clips = []

        for i, clip in enumerate(clips):
            out_path = f"{OUTPUT_DIR}/{job_id}_clip{i}.mp4"
            logger.info(f"✂️ Memotong clip {i+1}: {clip['start']} - {clip['end']}")

            ffmpeg_result = subprocess.run([
                "ffmpeg", "-y",
                "-i", raw_path,
                "-ss", clip["start"],
                "-to", clip["end"],
                "-c:v", "libx264",
                "-c:a", "aac",
                "-preset", "fast",
                out_path
            ], capture_output=True, text=True)

            if ffmpeg_result.returncode != 0:
                raise Exception(f"FFmpeg error pada clip {i}: {ffmpeg_result.stderr[-300:]}")

            logger.info(f"✅ Clip {i+1} selesai")
            result_clips.append({
                "index": i,
                "title": clip.get("title", f"Clip {i+1}"),
                "file": out_path,
                "start": clip["start"],
                "end": clip["end"]
            })

        if os.path.exists(raw_path):
            os.remove(raw_path)
            logger.info("🗑️ File raw dihapus")

        JOBS[job_id]["status"] = "ready"
        JOBS[job_id]["clips"] = result_clips
        logger.info(f"🎉 Job {job_id} selesai!")

    except Exception as e:
        logger.error(f"❌ Error pada job {job_id}: {str(e)}")
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)


@app.route("/process", methods=["POST"])
def start_process():
    data = request.json
    youtube_url = data.get("youtube_url")
    clips = data.get("clips", [])

    if not youtube_url or not clips:
        return jsonify({"error": "youtube_url dan clips wajib diisi"}), 400

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "clips": [], "error": None}
    logger.info(f"📥 Job baru diterima: {job_id}")

    thread = threading.Thread(target=process_video, args=(job_id, youtube_url, clips))
    thread.daemon = True
    thread.start()
    logger.info(f"🧵 Thread dimulai untuk job {job_id}")

    return jsonify({"job_id": job_id, "status": "queued"})


@app.route("/status/<job_id>", methods=["GET"])
def check_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job tidak ditemukan"}), 404
    return jsonify({
        "job_id": job_id,
        "status": job["status"],
        "error": job.get("error")
    })


@app.route("/result/<job_id>", methods=["GET"])
def get_result(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job tidak ditemukan"}), 404
    if job["status"] == "error":
        return jsonify({
            "status": "error",
            "detail": job.get("error", "Unknown error")
        }), 500
    if job["status"] != "ready":
        return jsonify({"status": job["status"]}), 202
    return jsonify({
        "job_id": job_id,
        "status": "ready",
        "clips": job["clips"]
    })


@app.route("/file/<job_id>/<int:clip_index>", methods=["GET"])
def get_file(job_id, clip_index):
    job = JOBS.get(job_id)
    if not job or job["status"] != "ready":
        return jsonify({"error": "Belum siap"}), 404
    clip = next((c for c in job["clips"] if c["index"] == clip_index), None)
    if not clip:
        return jsonify({"error": "Clip tidak ditemukan"}), 404
    return send_file(
        clip["file"],
        mimetype="video/mp4",
        as_attachment=True,
        download_name=f"{clip['title']}.mp4"
    )


@app.route("/", methods=["GET"])
def index():
    cookies_status = "✅ Cookies tersedia" if os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 0 else "❌ Cookies tidak ditemukan"
    return jsonify({
        "status": "Clipper API berjalan normal",
        "cookies": cookies_status,
        "active_jobs": len(JOBS)
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🚀 Server dimulai di port {port}")
    app.run(host="0.0.0.0", port=port)
