function(aidm_replace_once variable before after)
    string(FIND "${${variable}}" "${after}" already_applied)
    if(NOT already_applied EQUAL -1)
        return()
    endif()
    string(FIND "${${variable}}" "${before}" position)
    if(position EQUAL -1)
        message(FATAL_ERROR "OpenMVS weighted patch: source anchor changed")
    endif()
    string(LENGTH "${before}" length)
    math(EXPR end "${position} + ${length}")
    string(SUBSTRING "${${variable}}" ${end} -1 tail)
    string(FIND "${tail}" "${before}" repeated)
    if(NOT repeated EQUAL -1)
        message(FATAL_ERROR "OpenMVS weighted patch: source anchor is not unique")
    endif()
    string(REPLACE "${before}" "${after}" updated "${${variable}}")
    set(${variable} "${updated}" PARENT_SCOPE)
endfunction()

set(kernel_dir "${CMAKE_CURRENT_LIST_DIR}/../openmvs")
set(depth_source "${OPENMVS_SOURCE_DIR}/libs/MVS/DepthMap.cpp")
file(READ "${depth_source}" source)
aidm_replace_once(source "#include \"DepthMap.h\""
    "#include \"DepthMap.h\"\n#include \"AIDMWeightedPatch.h\"")
set(original "\tH *= float(nSizeStep);\n")
set(replacement [=[
	H *= float(nSizeStep);
	#if DENSE_NCC == DENSE_NCC_WEIGHTED
	const Weight& w = weightMap0[x0.y*image0.image.width()+x0.x];
	float sum, sumSq, num;
	if (!aidm::sampleWeightedPatch<(nSizeWindow+nSizeStep-1)/nSizeStep>(
		image1.image.ptr<float>(), image1.image.step1(),
		image1.image.width(), image1.image.height(), X.ptr(), H.val,
		w.weights, sum, sumSq, num))
		return thRobust;
	#else
]=])
aidm_replace_once(source "${original}" "${replacement}")
aidm_replace_once(source
    "\tASSERT(n == nTexels);\n\t// score similarity"
    "\tASSERT(n == nTexels);\n\t#endif\n\t// score similarity")
file(WRITE "${depth_source}" "${source}")
configure_file("${kernel_dir}/WeightedPatch.h"
    "${OPENMVS_SOURCE_DIR}/libs/MVS/AIDMWeightedPatch.h" COPYONLY)

set(build_source "${OPENMVS_SOURCE_DIR}/CMakeLists.txt")
file(READ "${build_source}" source)
set(test_include "\ninclude(\"${kernel_dir}/Tests.cmake\")\n")
string(FIND "${source}" "${test_include}" already_applied)
if(already_applied EQUAL -1)
    file(APPEND "${build_source}" "${test_include}")
endif()
