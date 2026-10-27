import argparse

from simulator import run_game


def main() -> None:
    parser = argparse.ArgumentParser(description="จำลองตลาดทดลองสำหรับ Track 3")
    parser.add_argument("--rounds", type=int, default=12, help="จำนวนรอบตลาด")
    parser.add_argument("--seed", type=int, default=7, help="ค่า seed สำหรับสุ่มสต็อกให้ทำซ้ำได้")
    parser.add_argument("--config", default=None, help="ไฟล์ JSON สำหรับตั้งค่าทีมและตลาด")
    parser.add_argument("--quiet", action="store_true", help="แสดงเฉพาะผลสรุป")
    args = parser.parse_args()
    run_game(rounds=args.rounds, seed=args.seed, verbose=not args.quiet, config_path=args.config)


if __name__ == "__main__":
    main()

