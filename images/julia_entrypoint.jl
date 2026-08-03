pushfirst!(DEPOT_PATH, "/opt/julia-depot")

using JuliaFormatter

if length(ARGS) != 1
    println(stderr, "usage: julia-format FILE")
    exit(2)
end

format_file(ARGS[1], overwrite = true, throw_on_error = true)
