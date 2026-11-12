// CLI: clique_cli <file-with-base92-matrix> <seconds> <threads> <seed> [max_out]
// prints one JSON object. Used for benchmarking; the miner uses the shared library (see clique_capi.cpp).
#include "clique_core.hpp"
#include <cstdio>
#include <fstream>
#include <sstream>
int main(int argc, char** argv) {
    if (argc < 5) { fprintf(stderr, "usage: %s enc_file seconds threads seed [max_out]\n", argv[0]); return 2; }
    std::ifstream f(argv[1]); std::stringstream ss; ss << f.rdbuf(); std::string s = ss.str();
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r')) s.pop_back();
    clq::Graph g; std::string err; auto t0 = std::chrono::steady_clock::now();
    if (!clq::decode_base92(s, g, &err)) { fprintf(stderr, "decode error: %s\n", err.c_str()); return 1; }
    double dec_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    double secs = atof(argv[2]); int threads = atoi(argv[3]); uint64_t seed = strtoull(argv[4], 0, 10); int max_out = argc > 5 ? atoi(argv[5]) : 100000;
    clq::Result R = clq::solve(g, secs, threads, seed);
    int bad = 0; for (auto& c : R.max_cliques) if ((int)c.v.size() != R.best || !clq::is_clique(g, c.v) || !clq::is_maximal(g, c.v)) bad++;
    for (auto& c : R.sub_cliques) if ((int)c.v.size() != R.best - 1 || !clq::is_clique(g, c.v) || !clq::is_maximal(g, c.v)) bad++;
    long long m = 0; for (int v = 0; v < g.n; v++) m += g.deg[v];
    printf("{\"n\":%d,\"density\":%.4f,\"decode_ms\":%.2f,\"best\":%d,\"time_to_best_ms\":%.1f,\"steps\":%llu,\"episodes\":%llu,\"elapsed_ms\":%.1f,\"n_max\":%zu,\"n_ls_max\":%zu,\"closure_bigger\":%d,\"n_sub\":%zu,\"invalid\":%d,",
           g.n, g.n > 1 ? (double)m / ((double)g.n * (g.n - 1)) : 0.0, dec_ms, R.best, R.time_to_best_ms, (unsigned long long)R.steps, (unsigned long long)R.episodes, R.elapsed_ms, R.max_cliques.size(), R.n_ls_max, (int)R.closure_found_bigger, R.sub_cliques.size(), bad);
    auto dump = [&](const char* name, std::vector<clq::CliqueRec>& L) {
        printf("\"%s\":[", name);
        for (size_t i = 0; i < L.size() && (int)i < max_out; i++) { printf("%s{\"v\":[", i ? "," : ""); for (size_t k = 0; k < L[i].v.size(); k++) printf("%s%d", k ? "," : "", L[i].v[k]); printf("],\"visits\":%u,\"episodes\":%u,\"first_ms\":%.1f}", L[i].visits, L[i].episodes, L[i].first_ms); }
        printf("]");
    };
    dump("max_cliques", R.max_cliques); printf(","); dump("sub_cliques", R.sub_cliques); printf("}\n");
    return 0;
}
