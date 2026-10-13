# pytauargus wheel build glue.
#
# The Python package (src/pytauargus) and the tests live in this repo. The
# native extension (_tauargus) is built from the pinned tauargus-engine
# checkout, which also provides the engine libs the extension links.
#
# The checkout comes from TAUARGUS_ENGINE_DIR (environment variable or
# -DTAUARGUS_ENGINE_DIR), defaulting to the sibling ../tauargus-engine. Its
# superbuild output (engine/build/lib) must exist before the wheel build;
# scripts/sync-venv.sh builds it.
cmake_minimum_required(VERSION 3.16)
project(pytauargus_wheel NONE)

if(NOT DEFINED TAUARGUS_ENGINE_DIR)
    if(DEFINED ENV{TAUARGUS_ENGINE_DIR})
        set(TAUARGUS_ENGINE_DIR $ENV{TAUARGUS_ENGINE_DIR})
    else()
        set(TAUARGUS_ENGINE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../tauargus-engine)
    endif()
endif()

# The binding CMakeLists defaults its extension output to the engine repo's
# package dir; point it at this repo's package instead.
set(TAUARGUS_PY_PKG_DIR ${CMAKE_CURRENT_SOURCE_DIR}/src/pytauargus CACHE PATH
    "pytauargus package dir (extension output + runtime libs)")

add_subdirectory(${TAUARGUS_ENGINE_DIR}/bindings/python
                 ${CMAKE_CURRENT_BINARY_DIR}/pybinding)
