from transformers import set_seed
import os
from tqdm import tqdm
import torch
import numpy as np
from tiger_utils import read_json, read_pickle, write_pickle, cosine_sim, write_json, split_inputs_by_interval
from typing import Union

from rerank import Reranker, Pair

def embed(texts: list[str], fn: Union[str, None], is_query: bool = False):
    BATCH_SIZE = 256

    if fn is not None and os.path.isfile(fn):
        return torch.from_numpy(np.load(fn))

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("Qwen/Qwen3-Embedding-8B", trust_remote_code=True).cuda()

    embeds = []
    for i in tqdm(range((len(texts) // BATCH_SIZE) + 1)):
        _texts = texts[i * BATCH_SIZE : (i + 1) * BATCH_SIZE]

        if len(_texts) == 0:
            break

        assert len(_texts) >= 1

        vec = model.encode(_texts, prompt_name='query', show_progress_bar=False) if is_query else model.encode(_texts, show_progress_bar=False)

        embeds.append(vec)

    embeds = np.vstack(embeds)

    if fn is not None:
        np.save(fn, embeds)

    embeds = torch.from_numpy(embeds)

    return embeds

def embed_dataset(dataset: str):
    embed_dir = f"./data/beaver/{dataset}/retrieval"
    create_directory(embed_dir)

    # embed questions
    qs = read_json(f'./data/beaver/{dataset}/dev.json')
    qs = [x['question'] for x in qs]
    q_embeds = embed(qs, f'{embed_dir}/q_embeds.npy', is_query=True)
    
    # embed tables
    corpus_tables = read_json(f'./data/beaver/{dataset}/dev_tables.json')
    # ts = [seq_table_for_embedding(corpus_tables[t]) for t in corpus_tables]
    trim = dataset in ['sp', 'nova']
    print(f'trim tables: {trim}')
    ts = [format_table(t, corpus_tables, use_instance=False, trim=trim) for t in corpus_tables]
    t_embeds = embed(ts, f'{embed_dir}/t_embeds.npy')

    score_fn = f"{embed_dir}/score.pkl"
    print(score_fn)
    sim_scores = cosine_sim(q_embeds, t_embeds)
    print(sim_scores.shape)
    write_pickle(sim_scores, score_fn)

def rerank_dataset(dataset: str):
    qs = read_json(f"./data/beaver/{dataset}/dev.json")
    corpus_tables = read_json(f'./data/beaver/{dataset}/dev_tables.json')
    trim = dataset in ['sp', 'nova']
    print(f'trim tables: {trim}')
    formatted_tables = {t: format_table(t, corpus_tables, use_instance=False, trim=trim) for t in corpus_tables}
    
    reranker = Reranker()

    # rerank based on the top-50 tables
    preds = get_pred_tables(dataset, k=50)
    reranked_preds = {}

    save_fn = f"./data/beaver/{dataset}/retrieval/reranked_preds.json"
    if os.path.isfile(save_fn):
        reranked_preds = read_json(save_fn)

    for q_idx in tqdm(q_idxs):
        if str(q_idx) in reranked_preds:
            continue

        pairs = []

        pred_tables = preds[q_idx]
        for pred_table in pred_tables:
            pairs.append(Pair(qs[q_idx]['question'], formatted_tables[pred_table]))

        rerank_scores = []
        num_partitions = 4
        for partition in range(num_partitions):
            _pairs = split_inputs_by_interval(pairs, num_partitions, partition)
            rerank_scores.append(reranker.rerank(_pairs))
        rerank_scores = torch.cat(rerank_scores)

        assert len(rerank_scores) == 50

        # re-rank scores
        top_table_idxs = torch.sort(rerank_scores, descending=True).indices
        top_tables = [pred_tables[i] for i in top_table_idxs]
        reranked_preds[str(q_idx)] = top_tables
    
        write_json(reranked_preds, save_fn)

if __name__ == "__main__":
    set_seed(1234)

    dataset = ['dw', 'sp', 'neutron', 'nova'][-1]
    k = 10

    print(dataset)

    # embed_dataset(dataset)
    # rerank_dataset(dataset)