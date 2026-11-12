// C API for the Python miner (ctypes). Build: see Makefile -> libcliquesolver.so
//
// int clq_solve(const char* enc, double seconds, int threads, uint64_t seed,
//               int* best, int* n_max, int* n_sub, int* flat, int flat_cap, uint32_t* counts, int counts_cap, double* t2b_ms)
//   enc       : NUL-terminated base92 adjacency string exactly as sent by the validator
//   flat      : receives n_max cliques of size `best` followed by n_sub cliques of size best-1, concatenated
//   counts    : per returned clique, number of independent local-search episodes that hit it (0 = found only by swap closure)
//   returns 0 on success, negative on error (-1 decode error, -2 empty result). Thread-safe / re-entrant.
#include "clique_core.hpp"
// Slots kept free for best-1 cliques so that a graph with very many maximum cliques cannot crowd them out.
static inline int max_sub_reserved(int counts_cap) { return counts_cap / 7; }
extern "C" {
int clq_solve(const char* enc, double seconds, int threads, uint64_t seed, int* best, int* n_max, int* n_sub,
              int* flat, int flat_cap, uint32_t* counts, int counts_cap, double* t2b_ms) {
    try {
        clq::Graph g; std::string err;
        if (!clq::decode_base92(std::string(enc), g, &err)) return -1;
        if (threads < 1) threads = 1;
        clq::Result R = clq::solve(g, seconds, threads, seed);
        if (R.closure_found_bigger && !R.bigger.empty() && clq::is_clique(g, R.bigger)) {
            // extremely rare: closure found a larger clique than the search. Extend greedily to maximal and return only it.
            std::vector<int> c = R.bigger; bool grown = true;
            while (grown) { grown = false; for (int v = 0; v < g.n && !grown; v++) { bool ok = true; for (int u : c) if (u == v || !g.adj(u, v)) { ok = false; break; } if (ok) { c.push_back(v); grown = true; } } }
            std::sort(c.begin(), c.end()); R.best = (int)c.size(); R.max_cliques.clear(); R.sub_cliques.clear();
            clq::CliqueRec rec; rec.v = c; rec.episodes = 1; rec.visits = 1; R.max_cliques.push_back(rec);
        }
        if (R.best <= 0 || R.max_cliques.empty()) return -2;
        int nm = 0, ns = 0, pos = 0;
        const int max_keep = counts_cap - std::min(counts_cap / 2, max_sub_reserved(counts_cap));
        for (auto& c : R.max_cliques) {
            if (pos + R.best > flat_cap || nm >= max_keep) break;
            if ((int)c.v.size() != R.best || !clq::is_clique(g, c.v) || !clq::is_maximal(g, c.v)) continue;   // defensive re-check
            for (int v : c.v) flat[pos++] = v;
            counts[nm + ns] = c.episodes; nm++;
        }
        for (auto& c : R.sub_cliques) {
            if (pos + R.best - 1 > flat_cap || nm + ns >= counts_cap) break;
            if ((int)c.v.size() != R.best - 1 || !clq::is_clique(g, c.v) || !clq::is_maximal(g, c.v)) continue;
            for (int v : c.v) flat[pos++] = v;
            counts[nm + ns] = c.episodes; ns++;
        }
        if (nm == 0) return -2;
        *best = R.best; *n_max = nm; *n_sub = ns; if (t2b_ms) *t2b_ms = R.time_to_best_ms;
        return 0;
    } catch (...) { return -3; }
}
int clq_num_nodes(const char* enc) { clq::Graph g; if (!clq::decode_base92(std::string(enc), g)) return -1; return g.n; }
}
