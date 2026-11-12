// CliqueAI solver core: randomized dynamic local search (DLS-MC/PLS family, pd=1) over bitset/complement-list
// adjacency, specialised for dense Erdos-Renyi-like graphs (n <= ~2000, density 0.7-0.95).
// Besides the best size it collects MANY DISTINCT maximum cliques and distinct maximal (best-1) cliques,
// with discovery statistics, because the subnet's reward has a uniqueness term.
#pragma once
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace clq {

struct Graph {
    int n = 0, W = 0;
    std::vector<uint64_t> bits;            // n*W adjacency bitset
    std::vector<std::vector<int>> nadj;    // complement adjacency lists (excluding self)
    std::vector<int> deg;
    inline bool adj(int a, int b) const { return (bits[(size_t)a * W + (b >> 6)] >> (b & 63)) & 1ULL; }
    inline const uint64_t* row(int a) const { return &bits[(size_t)a * W]; }
    void init(int n_) { n = n_; W = (n + 63) / 64; bits.assign((size_t)n * W, 0); }
    inline void add_edge(int a, int b) {
        bits[(size_t)a * W + (b >> 6)] |= 1ULL << (b & 63);
        bits[(size_t)b * W + (a >> 6)] |= 1ULL << (a & 63);
    }
    void finalize() {
        nadj.assign(n, {}); deg.assign(n, 0);
        for (int a = 0; a < n; a++) {
            for (int b = 0; b < n; b++) if (a != b) { if (adj(a, b)) deg[a]++; else nadj[a].push_back(b); }
        }
    }
};

// ---- base92 decoder for the validator's encoded_matrix (see CliqueAI/graph/codec.py) ----
inline bool decode_base92(const std::string& s, Graph& g, std::string* err = nullptr) {
    static int8_t idx[128]; static bool init = false;
    if (!init) { memset(idx, -1, sizeof idx); int k = 0; for (int c = 32; c < 127; c++) if (c != 34 && c != 39 && c != 92) idx[c] = (int8_t)k++; init = true; }
    const int HDR = 5, CB = 1246, CD = 191;
    auto fail = [&](const char* m) { if (err) *err = m; return false; };
    if ((int)s.size() < HDR) return fail("truncated header");
    long long n = 0;
    for (int i = 0; i < HDR; i++) { unsigned char c = s[i]; if (c >= 128 || idx[c] < 0) return fail("bad char"); n = n * 92 + idx[c]; }
    if (n <= 0 || n > 20000) return fail("bad n");
    long long total = n * (n - 1) / 2, full = total / CB, rem = total % CB;
    auto min_digits = [](long long r) { if (r <= 0) return 0; // smallest m with 92^m >= 2^r
        long double need = r * 0.30102999566398119521L / 1.96378782734555524L; int m = (int)need; if (m < 1) m = 1;
        // exact check with big arithmetic is overkill; mirror python loop using long double log with correction
        auto ge = [&](int mm) { return mm * 1.96378782734555524L >= r * 0.30102999566398119521L - 1e-12L; };
        while (m > 1 && ge(m - 1)) m--;
        while (!ge(m)) m++;
        return m; };
    int tail = min_digits(rem);
    if ((long long)s.size() != HDR + full * CD + tail) return fail("length mismatch");
    g.init((int)n);
    size_t pos = HDR; int i = 0, j = 1;
    const int L = 20; uint64_t limb[L];
    auto emit = [&](int nbits) { // bits MSB first
        for (int b = nbits - 1; b >= 0; b--) {
            if ((limb[b >> 6] >> (b & 63)) & 1ULL) g.add_edge(i, j);
            if (++j == n) { i++; j = i + 1; }
        }
    };
    auto read_chunk = [&](int digits) -> bool {
        memset(limb, 0, sizeof limb);
        for (int d = 0; d < digits; d++) {
            unsigned char c = s[pos++]; if (c >= 128 || idx[c] < 0) return false;
            unsigned __int128 carry = (unsigned)idx[c];
            for (int k = 0; k < L; k++) { unsigned __int128 v = (unsigned __int128)limb[k] * 92 + carry; limb[k] = (uint64_t)v; carry = v >> 64; }
        }
        return true;
    };
    for (long long c = 0; c < full; c++) { if (!read_chunk(CD)) return fail("bad char"); emit(CB); }
    if (rem) { if (!read_chunk(tail)) return fail("bad char"); emit((int)rem); }
    g.finalize();
    return true;
}

struct Rng { // xoshiro256**
    uint64_t s[4];
    static inline uint64_t rotl(uint64_t x, int k) { return (x << k) | (x >> (64 - k)); }
    explicit Rng(uint64_t seed) { for (auto& x : s) { seed += 0x9E3779B97F4A7C15ULL; uint64_t z = seed; z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL; z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL; x = z ^ (z >> 31); } }
    inline uint64_t next() { uint64_t r = rotl(s[1] * 5, 7) * 9, t = s[1] << 17; s[2] ^= s[0]; s[3] ^= s[1]; s[1] ^= s[2]; s[0] ^= s[3]; s[2] ^= t; s[3] = rotl(s[3], 45); return r; }
    inline uint32_t below(uint32_t n) { return (uint32_t)(((uint64_t)(uint32_t)next() * n) >> 32); }
};

struct CliqueRec { std::vector<int> v; uint32_t visits = 0; uint32_t episodes = 0; double first_ms = 0; };
typedef std::unordered_map<uint64_t, CliqueRec> CliqueMap;

struct Shared {
    std::atomic<int> best{0};
    std::atomic<bool> stop{false};
    std::chrono::steady_clock::time_point t0, deadline;
};

struct Params {
    int max_store_best = 20000;   // cap on distinct max cliques stored per thread
    int max_store_sub = 4000;     // cap on distinct (best-1) maximal cliques per thread
    int full_restart_every = 64;  // every k-th perturbation is a restart from scratch
    double closure_frac = 0.04;   // fraction of the time budget reserved for swap closure (0 disables)
};

struct Searcher {
    const Graph& g; Shared& sh; Params P; Rng rng; int n;
    std::vector<int> clq, cpos, miss, xs, NI, NIpos, NL, NLpos, stampUsed, stampOrig;
    std::vector<uint64_t> zob; uint64_t h = 0; int phase = 0; uint64_t steps = 0, episodes = 0;
    CliqueMap bestMap, subMap; int localBest = 0; int subSize = -1; bool freshEpisode = true;
    double time_to_best_ms = 1e18;

    Searcher(const Graph& g_, Shared& sh_, uint64_t seed, const Params& p) : g(g_), sh(sh_), P(p), rng(seed), n(g_.n) {
        cpos.assign(n, -1); miss.assign(n, 0); xs.assign(n, 0); NIpos.assign(n, -1); NLpos.assign(n, -1);
        stampUsed.assign(n, -1); stampOrig.assign(n, -1); zob.resize(n);
        Rng zr(0xC0FFEE1234ULL); for (int i = 0; i < n; i++) zob[i] = zr.next();   // same zobrist table in all threads
        clq.reserve(n); NI.reserve(n); NL.reserve(n);
        reset();
    }
    inline void setAdd(std::vector<int>& S, std::vector<int>& pos, int v) { pos[v] = (int)S.size(); S.push_back(v); }
    inline void setDel(std::vector<int>& S, std::vector<int>& pos, int v) { int p = pos[v], l = S.back(); S[p] = l; pos[l] = p; S.pop_back(); pos[v] = -1; }
    void reset() {
        clq.clear(); NI.clear(); NL.clear(); h = 0;
        for (int v = 0; v < n; v++) { cpos[v] = -1; miss[v] = 0; xs[v] = 0; NLpos[v] = -1; NIpos[v] = v; NI.push_back(v); }
    }
    inline void addV(int u) { // requires miss[u]==0 && u not in clique
        setDel(NI, NIpos, u); cpos[u] = (int)clq.size(); clq.push_back(u); h ^= zob[u];
        for (int w : g.nadj[u]) {
            int m = ++miss[w]; xs[w] ^= u;
            if (m == 1) { setDel(NI, NIpos, w); setAdd(NL, NLpos, w); }
            else if (m == 2) { setDel(NL, NLpos, w); }
        }
    }
    inline void delV(int u) { // u in clique
        int p = cpos[u], l = clq.back(); clq[p] = l; cpos[l] = p; clq.pop_back(); cpos[u] = -1; h ^= zob[u];
        for (int w : g.nadj[u]) {
            int m = --miss[w]; xs[w] ^= u;
            if (m == 0) { setDel(NL, NLpos, w); setAdd(NI, NIpos, w); }
            else if (m == 1) { setAdd(NL, NLpos, w); }
        }
        setAdd(NI, NIpos, u); // u is adjacent to all remaining clique vertices
    }
    inline double now_ms() { return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - sh.t0).count(); }
    void record() { // current clique is maximal (NI empty)
        int sz = (int)clq.size();
        int gb = sh.best.load(std::memory_order_relaxed);
        if (sz > gb) { while (sz > gb && !sh.best.compare_exchange_weak(gb, sz)) {} gb = std::max(gb, sz); }
        if (sz < gb - 1) return;
        if (sz > localBest || gb > localBest) {
            int nb = std::max(sz, gb);
            if (nb > localBest) {
                if (nb == localBest + 1) { subMap.swap(bestMap); bestMap.clear(); subSize = localBest; }
                else { subMap.clear(); bestMap.clear(); subSize = nb - 1; }
                localBest = nb; time_to_best_ms = (sz == nb) ? now_ms() : 1e18;   // 1e18: best size learnt from another thread
            }
        }
        if (sz == localBest && time_to_best_ms >= 1e17) time_to_best_ms = now_ms();
        CliqueMap& M = (sz == localBest) ? bestMap : subMap;
        int cap = (sz == localBest) ? P.max_store_best : P.max_store_sub;
        auto it = M.find(h);
        if (it == M.end()) {
            if ((int)M.size() >= cap) return;
            CliqueRec r; r.v = clq; std::sort(r.v.begin(), r.v.end()); r.visits = 1; r.episodes = 1; r.first_ms = now_ms();
            M.emplace(h, std::move(r));
        } else { it->second.visits++; if (freshEpisode) it->second.episodes++; }
        freshEpisode = false;
    }
    inline void expand() { while (!NI.empty()) { addV(NI[rng.below((uint32_t)NI.size())]); steps++; } }
    void run() {
        if (n <= 0) return;
        addV(NI[rng.below((uint32_t)NI.size())]);
        uint64_t pert = 0;
        while (true) {
            expand(); record();
            // plateau search
            phase++; int origLeft = (int)clq.size();
            for (int v : clq) stampOrig[v] = phase;
            while (true) {
                // choose random unused vertex from NL
                int v = -1; int sz = (int)NL.size();
                if (sz == 0) break;
                for (int t = 0; t < 4 && v < 0; t++) { int c = NL[rng.below((uint32_t)sz)]; if (stampUsed[c] != phase) v = c; }
                if (v < 0) { int st = rng.below((uint32_t)sz); for (int k = 0; k < sz; k++) { int c = NL[(st + k) % sz]; if (stampUsed[c] != phase) { v = c; break; } } }
                if (v < 0) break;
                int u = xs[v];
                delV(u); addV(v); steps += 2;
                stampUsed[u] = phase; stampUsed[v] = phase;
                if (stampOrig[u] == phase) origLeft--;
                if (!NI.empty()) { expand(); record(); phase++; origLeft = (int)clq.size(); for (int x : clq) stampOrig[x] = phase; continue; }
                record();
                if (origLeft == 0) break;
            }
            if ((int)clq.size() == n) { sh.stop.store(true); break; }   // the whole graph is one clique: nothing left to search
            if ((pert & 15) == 0) {
                if (sh.stop.load(std::memory_order_relaxed) || std::chrono::steady_clock::now() >= sh.deadline) break;
            }
            // perturbation
            pert++; episodes++; freshEpisode = true;
            if (P.full_restart_every > 0 && pert % P.full_restart_every == 0) {
                reset(); addV(NI[rng.below((uint32_t)NI.size())]);
            } else {
                int v; do { v = (int)rng.below((uint32_t)n); } while (cpos[v] >= 0);
                // remove all clique vertices not adjacent to v, then add v
                for (int w : g.nadj[v]) if (cpos[w] >= 0) delV(w);
                addV(v); steps++;
            }
        }
    }
};

struct Result {
    int best = 0; double time_to_best_ms = 0; uint64_t steps = 0, episodes = 0; double elapsed_ms = 0;
    std::vector<CliqueRec> max_cliques, sub_cliques;   // distinct, merged across threads
    size_t n_ls_max = 0; bool closure_found_bigger = false; std::vector<int> bigger;
};

inline bool is_clique(const Graph& g, const std::vector<int>& c) { for (size_t i = 0; i < c.size(); i++) for (size_t j = i + 1; j < c.size(); j++) if (!g.adj(c[i], c[j])) return false; return true; }
inline bool is_maximal(const Graph& g, const std::vector<int>& c) {
    std::vector<uint64_t> acc(g.W, ~0ULL);
    for (int v : c) { const uint64_t* r = g.row(v); for (int k = 0; k < g.W; k++) acc[k] &= r[k]; }
    for (int v = 0; v < g.n; v++) if ((acc[v >> 6] >> (v & 63)) & 1ULL) return false;   // members are not self-adjacent so they drop out
    return true;
}


// ---- swap closure -------------------------------------------------------------------------------------------
// Starting from the maximum cliques found by local search, enumerate every clique of the same size reachable by
// replacing one vertex (1-swap) or two vertices (2-swap). Maximum cliques of G(n,p) come in swap-connected
// clusters, so this turns "one hit per cluster" into the complete cluster at negligible cost.
// Cliques first reached here get episodes==0 (never hit directly by the randomized search).
// Returns true if a strictly larger clique was discovered (written to `bigger`).
inline bool swap_closure(const Graph& g, std::vector<CliqueRec>& cliques, int best, std::chrono::steady_clock::time_point t0,
                         std::chrono::steady_clock::time_point deadline, size_t cap, std::vector<int>* bigger = nullptr) {
    const int n = g.n; if (cliques.empty()) return false;
    std::vector<uint64_t> zob(n); { Rng zr(0xC0FFEE1234ULL); for (int i = 0; i < n; i++) zob[i] = zr.next(); }
    auto hashOf = [&](const std::vector<int>& c) { uint64_t h = 0; for (int v : c) h ^= zob[v]; return h; };
    std::unordered_map<uint64_t, int> seen; seen.reserve(cliques.size() * 4 + 64);
    for (size_t i = 0; i < cliques.size(); i++) seen.emplace(hashOf(cliques[i].v), (int)i);
    std::vector<int> miss(n), m1(n), m2(n), inC(n, 0), L;
    auto ms = [&] { return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count(); };
    for (size_t qi = 0; qi < cliques.size(); qi++) {
        if ((qi & 15) == 0 && std::chrono::steady_clock::now() >= deadline) break;
        if (cliques.size() >= cap) break;
        const std::vector<int> C = cliques[qi].v; const uint64_t hC = hashOf(C);
        std::fill(miss.begin(), miss.end(), 0);
        for (int u : C) inC[u] = 1;
        for (int u : C) for (int w : g.nadj[u]) { int m = miss[w]++; if (m == 0) m1[w] = u; else if (m == 1) m2[w] = u; }
        L.clear(); for (int v = 0; v < n; v++) if (!inC[v] && miss[v] >= 1 && miss[v] <= 2) L.push_back(v);
        auto emit = [&](uint64_t h, int out1, int out2, int in1, int in2) {
            if (seen.count(h)) return;
            CliqueRec r; r.v.reserve(C.size());
            for (int x : C) if (x != out1 && x != out2) r.v.push_back(x);
            r.v.push_back(in1); if (in2 >= 0) r.v.push_back(in2);
            std::sort(r.v.begin(), r.v.end()); r.visits = 0; r.episodes = 0; r.first_ms = ms();
            seen.emplace(h, (int)cliques.size()); cliques.push_back(std::move(r));
        };
        for (size_t a = 0; a < L.size(); a++) {
            int v = L[a];
            if (miss[v] == 1) {            // 1-swap: C - m1[v] + v
                emit(hC ^ zob[m1[v]] ^ zob[v], m1[v], -1, v, -1);
            }
            for (size_t b = a + 1; b < L.size(); b++) {
                int w = L[b]; if (!g.adj(v, w)) continue;
                // union of missing sets
                int s[4], k = 0; s[k++] = m1[v]; if (miss[v] == 2) s[k++] = m2[v];
                int t1 = m1[w], t2 = miss[w] == 2 ? m2[w] : -1;
                if (t1 != s[0] && (k < 2 || t1 != s[1])) s[k++] = t1;
                if (t2 >= 0 && t2 != s[0] && (k < 2 || t2 != s[1]) && (k < 3 || t2 != s[2])) s[k++] = t2;
                if (k == 1) {              // both miss only the same vertex: C - s0 + v + w is a LARGER clique
                    if (bigger) { bigger->clear(); for (int x : C) if (x != s[0]) bigger->push_back(x); bigger->push_back(v); bigger->push_back(w); std::sort(bigger->begin(), bigger->end()); }
                    for (int u : C) inC[u] = 0;
                    return true;
                }
                if (k == 2) emit(hC ^ zob[s[0]] ^ zob[s[1]] ^ zob[v] ^ zob[w], s[0], s[1], v, w);
            }
        }
        for (int u : C) inC[u] = 0;
    }
    (void)best;
    return false;
}

inline Result solve(const Graph& g, double seconds, int threads, uint64_t seed, const Params& P = Params()) {
    Shared sh; sh.t0 = std::chrono::steady_clock::now();
    auto hard = sh.t0 + std::chrono::microseconds((long long)(seconds * 1e6));
    double reserve = std::min(0.25, std::max(0.02, seconds * P.closure_frac));
    sh.deadline = sh.t0 + std::chrono::microseconds((long long)((seconds - reserve) * 1e6));
    std::vector<Searcher*> S; for (int t = 0; t < threads; t++) S.push_back(new Searcher(g, sh, seed * 1000003ULL + 7919ULL * t + 1, P));
    std::vector<std::thread> th; for (int t = 1; t < threads; t++) th.emplace_back([&, t] { S[t]->run(); });
    S[0]->run(); for (auto& x : th) x.join();
    Result R; R.best = sh.best.load(); R.time_to_best_ms = 1e18;
    CliqueMap mb, ms;
    auto merge = [](CliqueMap& into, CliqueMap& from) { for (auto& kv : from) { auto it = into.find(kv.first); if (it == into.end()) into.emplace(kv.first, std::move(kv.second)); else { it->second.visits += kv.second.visits; it->second.episodes += kv.second.episodes; it->second.first_ms = std::min(it->second.first_ms, kv.second.first_ms); } } };
    for (auto* s : S) {
        R.steps += s->steps; R.episodes += s->episodes;
        if (s->localBest == R.best) { merge(mb, s->bestMap); if (s->subSize == R.best - 1) merge(ms, s->subMap); R.time_to_best_ms = std::min(R.time_to_best_ms, s->time_to_best_ms); }
        else if (s->localBest == R.best - 1) merge(ms, s->bestMap);
    }
    for (auto& kv : mb) R.max_cliques.push_back(std::move(kv.second));
    for (auto& kv : ms) R.sub_cliques.push_back(std::move(kv.second));
    R.n_ls_max = R.max_cliques.size();
    if (P.closure_frac > 0) { std::vector<int> big; std::sort(R.max_cliques.begin(), R.max_cliques.end(), [](const CliqueRec& a, const CliqueRec& b) { return a.first_ms < b.first_ms; });
        if (swap_closure(g, R.max_cliques, R.best, sh.t0, hard, (size_t)P.max_store_best * 4, &big)) { R.closure_found_bigger = true; R.bigger = big; } }
    auto byFirst = [](const CliqueRec& a, const CliqueRec& b) { return a.first_ms < b.first_ms; };
    std::sort(R.max_cliques.begin(), R.max_cliques.end(), byFirst); std::sort(R.sub_cliques.begin(), R.sub_cliques.end(), byFirst);
    for (auto* s : S) delete s;
    R.elapsed_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - sh.t0).count();
    return R;
}

} // namespace clq
