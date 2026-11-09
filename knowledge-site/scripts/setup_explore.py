"""Install an offline embedding model, or build/update 知识求索's public index."""
import argparse
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-model', action='store_true', help='Download BAAI/bge-small-zh-v1.5 (no notes uploaded)')
    parser.add_argument('--db', type=Path, default=Path(os.getenv('KB_DB_PATH', BASE / 'work/site.db')))
    parser.add_argument('--model', type=Path, default=Path(os.getenv('KB_EXPLORE_EMBEDDING_MODEL', BASE / 'work/models/bge-small-zh-v1.5')))
    parser.add_argument('--vectors', action='store_true', help='Compute local vectors after indexing')
    args = parser.parse_args()
    if args.download_model:
        from huggingface_hub import snapshot_download
        snapshot_download('BAAI/bge-small-zh-v1.5', local_dir=str(args.model),
                          allow_patterns=['*.json', '*.safetensors', 'vocab.txt', 'tokenizer.*', '1_Pooling/*'])
        print(f'Offline model installed: {args.model}')
    if not args.db.is_file():
        if args.download_model:
            return
        parser.error('Database does not exist. Start the site or preview first.')
    from storage import connect
    from explore_search import ExploreIndex, LocalEmbeddings, initialize_explore
    with connect(args.db) as db:
        initialize_explore(db)
        index = ExploreIndex(LocalEmbeddings(args.model))
        index.sync(db)
        db.commit()
        count = index.build_vectors(db) if args.vectors else 0
        documents = db.execute('SELECT count(*) FROM explore_documents').fetchone()[0]
        chunks = db.execute('SELECT count(*) FROM explore_chunks').fetchone()[0]
        print(f'知识求索: {documents} public articles, {chunks} passages, {count} refreshed vectors')


if __name__ == '__main__':
    main()
