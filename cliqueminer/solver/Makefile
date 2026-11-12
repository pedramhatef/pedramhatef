CXX ?= g++
CXXFLAGS ?= -O3 -march=native -std=c++17 -pthread -Wall -Wextra
all: clique_cli libcliquesolver.so
clique_cli: clique_cli.cpp clique_core.hpp
	$(CXX) $(CXXFLAGS) -o $@ clique_cli.cpp
libcliquesolver.so: clique_capi.cpp clique_core.hpp
	$(CXX) $(CXXFLAGS) -fPIC -shared -o $@ clique_capi.cpp
clean:
	rm -f clique_cli libcliquesolver.so
.PHONY: all clean
