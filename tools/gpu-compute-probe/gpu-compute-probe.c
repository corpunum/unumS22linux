#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define N 256u
#define VKCHECK(x) do { VkResult _r = (x); if (_r != VK_SUCCESS) { fprintf(stderr, "%s failed: %d\n", #x, _r); return 1; } } while (0)

static PFN_vkGetInstanceProcAddr gip;
#define I(name) PFN_##name name = (PFN_##name)gip(instance, #name)

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: %s compute.spv\n", argv[0]); return 2; }
    void *lib = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!lib) { fprintf(stderr, "dlopen libvulkan.so.1: %s\n", dlerror()); return 1; }
    gip = (PFN_vkGetInstanceProcAddr)dlsym(lib, "vkGetInstanceProcAddr");
    if (!gip) { fprintf(stderr, "missing vkGetInstanceProcAddr\n"); return 1; }
    PFN_vkEnumerateInstanceVersion eiv = (PFN_vkEnumerateInstanceVersion)gip(NULL, "vkEnumerateInstanceVersion");
    uint32_t api = VK_API_VERSION_1_0; if (eiv) eiv(&api);
    printf("loader_api=%u.%u.%u\n", VK_VERSION_MAJOR(api), VK_VERSION_MINOR(api), VK_VERSION_PATCH(api));
    VkApplicationInfo ai = { VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "gpu-compute-probe", 1, "gpu-compute-probe", 1, VK_API_VERSION_1_0 };
    VkInstanceCreateInfo ici = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &ai, 0, NULL, 0, NULL };
    PFN_vkCreateInstance ci = (PFN_vkCreateInstance)gip(NULL, "vkCreateInstance"); VkInstance instance;
    VKCHECK(ci(&ici, NULL, &instance));
    I(vkDestroyInstance); I(vkEnumeratePhysicalDevices); I(vkGetPhysicalDeviceProperties);
    I(vkGetPhysicalDeviceQueueFamilyProperties); I(vkCreateDevice); I(vkGetPhysicalDeviceMemoryProperties);
    uint32_t count = 0; VKCHECK(vkEnumeratePhysicalDevices(instance, &count, NULL));
    VkPhysicalDevice *devs = calloc(count, sizeof(*devs)); VKCHECK(vkEnumeratePhysicalDevices(instance, &count, devs));
    VkPhysicalDevice phys = VK_NULL_HANDLE; uint32_t qfam = UINT32_MAX; VkPhysicalDeviceProperties pp;
    for (uint32_t d = 0; d < count && !phys; ++d) {
        VkPhysicalDeviceProperties p; vkGetPhysicalDeviceProperties(devs[d], &p);
        uint32_t nq = 0; vkGetPhysicalDeviceQueueFamilyProperties(devs[d], &nq, NULL);
        VkQueueFamilyProperties *qs = calloc(nq, sizeof(*qs)); vkGetPhysicalDeviceQueueFamilyProperties(devs[d], &nq, qs);
        for (uint32_t q = 0; q < nq; ++q) if ((qs[q].queueFlags & VK_QUEUE_COMPUTE_BIT) && (p.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU || p.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU)) { phys = devs[d]; qfam = q; pp = p; break; }
        free(qs);
    }
    free(devs);
    if (!phys) { fprintf(stderr, "no non-CPU compute device\n"); return 3; }
    printf("device=%s type=%u api=%u.%u.%u driver=%u\n", pp.deviceName, pp.deviceType, VK_VERSION_MAJOR(pp.apiVersion), VK_VERSION_MINOR(pp.apiVersion), VK_VERSION_PATCH(pp.apiVersion), pp.driverVersion);
    float priority = 1.0f; VkDeviceQueueCreateInfo qci = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, qfam, 1, &priority };
    VkDeviceCreateInfo dci = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, NULL, 0, 1, &qci, 0, NULL, 0, NULL, NULL }; VkDevice device;
    PFN_vkGetDeviceProcAddr gdp = NULL; VKCHECK(vkCreateDevice(phys, &dci, NULL, &device));
    gdp = (PFN_vkGetDeviceProcAddr)gip(instance, "vkGetDeviceProcAddr");
#define D(name) PFN_##name name = (PFN_##name)gdp(device, #name)
    D(vkDestroyDevice); D(vkGetDeviceQueue); D(vkCreateBuffer); D(vkGetBufferMemoryRequirements); D(vkAllocateMemory); D(vkBindBufferMemory); D(vkMapMemory); D(vkUnmapMemory); D(vkCreateDescriptorSetLayout); D(vkCreatePipelineLayout); D(vkCreateShaderModule); D(vkCreateComputePipelines); D(vkCreateDescriptorPool); D(vkAllocateDescriptorSets); D(vkUpdateDescriptorSets); D(vkCreateCommandPool); D(vkAllocateCommandBuffers); D(vkBeginCommandBuffer); D(vkCmdBindPipeline); D(vkCmdBindDescriptorSets); D(vkCmdDispatch); D(vkCmdPipelineBarrier); D(vkEndCommandBuffer); D(vkCreateFence); D(vkQueueSubmit); D(vkWaitForFences); D(vkDestroyFence); D(vkFreeCommandBuffers); D(vkDestroyCommandPool); D(vkDestroyDescriptorPool); D(vkDestroyPipeline); D(vkDestroyPipelineLayout); D(vkDestroyShaderModule); D(vkDestroyDescriptorSetLayout); D(vkDestroyBuffer); D(vkFreeMemory);
    VkQueue queue; vkGetDeviceQueue(device, qfam, 0, &queue);
    FILE *f = fopen(argv[1], "rb"); if (!f) { perror(argv[1]); return 1; } fseek(f, 0, SEEK_END); long sz = ftell(f); rewind(f); if (sz < 4 || sz > 1024*1024 || (sz & 3)) { fprintf(stderr, "invalid SPIR-V size: %ld\n", sz); return 1; } uint32_t *code = malloc((size_t)sz); if (!code || fread(code, 1, (size_t)sz, f) != (size_t)sz) return 1; fclose(f); if (code[0] != 0x07230203u) { fprintf(stderr, "bad SPIR-V magic\n"); return 1; }
    VkShaderModuleCreateInfo smi = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, NULL, 0, (size_t)sz, code }; VkShaderModule sm; VKCHECK(vkCreateShaderModule(device, &smi, NULL, &sm)); free(code);
    VkBufferCreateInfo bci = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, N*4u, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, VK_SHARING_MODE_EXCLUSIVE, 1, &qfam }; VkBuffer buf; VKCHECK(vkCreateBuffer(device, &bci, NULL, &buf));
    VkMemoryRequirements mr; vkGetBufferMemoryRequirements(device, buf, &mr); VkPhysicalDeviceMemoryProperties mp; vkGetPhysicalDeviceMemoryProperties(phys, &mp); uint32_t mi = UINT32_MAX;
    for (uint32_t i=0;i<mp.memoryTypeCount;i++) if ((mr.memoryTypeBits&(1u<<i)) && (mp.memoryTypes[i].propertyFlags&(VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) == (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) { mi=i; break; }
    if (mi == UINT32_MAX) { fprintf(stderr,"no HOST_VISIBLE|HOST_COHERENT memory\n"); return 1; } VkMemoryAllocateInfo mai={VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,NULL,mr.size,mi}; VkDeviceMemory mem; VKCHECK(vkAllocateMemory(device,&mai,NULL,&mem)); VKCHECK(vkBindBufferMemory(device,buf,mem,0));
    VkDescriptorSetLayoutBinding lb={0, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1,VK_SHADER_STAGE_COMPUTE_BIT,NULL}; VkDescriptorSetLayoutCreateInfo lci={VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,NULL,0,1,&lb}; VkDescriptorSetLayout sl; VKCHECK(vkCreateDescriptorSetLayout(device,&lci,NULL,&sl)); VkPipelineLayoutCreateInfo plci={VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,NULL,0,1,&sl,0,NULL}; VkPipelineLayout pl; VKCHECK(vkCreatePipelineLayout(device,&plci,NULL,&pl)); VkPipelineShaderStageCreateInfo ss={VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,NULL,0,VK_SHADER_STAGE_COMPUTE_BIT,sm,"main",NULL}; VkComputePipelineCreateInfo pci={VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,NULL,0,ss,pl,VK_NULL_HANDLE,-1}; VkPipeline pipe; VKCHECK(vkCreateComputePipelines(device,VK_NULL_HANDLE,1,&pci,NULL,&pipe));
    VkDescriptorPoolSize ps={VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1}; VkDescriptorPoolCreateInfo dpci={VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,NULL,0,1,1,&ps}; VkDescriptorPool pool; VKCHECK(vkCreateDescriptorPool(device,&dpci,NULL,&pool)); VkDescriptorSet ds; VkDescriptorSetAllocateInfo dai={VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,NULL,pool,1,&sl}; VKCHECK(vkAllocateDescriptorSets(device,&dai,&ds)); VkDescriptorBufferInfo dbi={buf,0,N*4u}; VkWriteDescriptorSet wr={VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,NULL,ds,0,0,1,VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,NULL,&dbi,NULL}; vkUpdateDescriptorSets(device,1,&wr,0,NULL);
    if (getenv("S22_GPU_PREPARE_ONLY")) {
        printf("PREPARE_ONLY: descriptors created; no compute commands submitted; NOT a correctness pass\n");
        vkDestroyDescriptorPool(device,pool,NULL); vkDestroyPipeline(device,pipe,NULL);
        vkDestroyPipelineLayout(device,pl,NULL); vkDestroyShaderModule(device,sm,NULL);
        vkDestroyDescriptorSetLayout(device,sl,NULL); vkDestroyBuffer(device,buf,NULL);
        vkFreeMemory(device,mem,NULL); vkDestroyDevice(device,NULL); vkDestroyInstance(instance,NULL);
        dlclose(lib); return 0;
    }
    if (getenv("S22_GPU_RECORD_ONLY")) {
        VkCommandPoolCreateInfo cpci={VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,NULL,0,qfam}; VkCommandPool cp;
        VKCHECK(vkCreateCommandPool(device,&cpci,NULL,&cp));
        VkCommandBufferAllocateInfo cai={VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,NULL,cp,VK_COMMAND_BUFFER_LEVEL_PRIMARY,1}; VkCommandBuffer cb;
        VKCHECK(vkAllocateCommandBuffers(device,&cai,&cb));
        VkCommandBufferBeginInfo cbi={VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,NULL,VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,NULL};
        VKCHECK(vkBeginCommandBuffer(cb,&cbi));
        vkCmdBindPipeline(cb,VK_PIPELINE_BIND_POINT_COMPUTE,pipe);
        vkCmdBindDescriptorSets(cb,VK_PIPELINE_BIND_POINT_COMPUTE,pl,0,1,&ds,0,NULL);
        vkCmdDispatch(cb,4,1,1);
        VkMemoryBarrier mb={VK_STRUCTURE_TYPE_MEMORY_BARRIER,NULL,VK_ACCESS_SHADER_WRITE_BIT,VK_ACCESS_HOST_READ_BIT};
        vkCmdPipelineBarrier(cb,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&mb,0,NULL,0,NULL);
        VKCHECK(vkEndCommandBuffer(cb));
        printf("RECORD_ONLY: vkCmdDispatch and vkEndCommandBuffer recorded; NO vkCreateFence/vkQueueSubmit; NOT a correctness pass\n");
        fflush(stdout);
        vkFreeCommandBuffers(device,cp,1,&cb); vkDestroyCommandPool(device,cp,NULL);
        vkDestroyDescriptorPool(device,pool,NULL); vkDestroyPipeline(device,pipe,NULL);
        vkDestroyPipelineLayout(device,pl,NULL); vkDestroyShaderModule(device,sm,NULL);
        vkDestroyDescriptorSetLayout(device,sl,NULL); vkDestroyBuffer(device,buf,NULL);
        vkFreeMemory(device,mem,NULL); vkDestroyDevice(device,NULL); vkDestroyInstance(instance,NULL);
        dlclose(lib); return 0;
    }
    uint32_t *initial; VKCHECK(vkMapMemory(device,mem,0,N*4u,0,(void**)&initial)); memset(initial,0xA5,N*4u); vkUnmapMemory(device,mem);
    VkCommandPoolCreateInfo cpci={VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,NULL,0,qfam}; VkCommandPool cp; VKCHECK(vkCreateCommandPool(device,&cpci,NULL,&cp));
    VkCommandBufferAllocateInfo cai={VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,NULL,cp,VK_COMMAND_BUFFER_LEVEL_PRIMARY,1}; VkCommandBuffer cb; VKCHECK(vkAllocateCommandBuffers(device,&cai,&cb)); VkCommandBufferBeginInfo cbi={VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,NULL,VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,NULL}; VKCHECK(vkBeginCommandBuffer(cb,&cbi)); vkCmdBindPipeline(cb,VK_PIPELINE_BIND_POINT_COMPUTE,pipe); vkCmdBindDescriptorSets(cb,VK_PIPELINE_BIND_POINT_COMPUTE,pl,0,1,&ds,0,NULL); vkCmdDispatch(cb,4,1,1);
    VkMemoryBarrier mb={VK_STRUCTURE_TYPE_MEMORY_BARRIER,NULL,VK_ACCESS_SHADER_WRITE_BIT,VK_ACCESS_HOST_READ_BIT}; vkCmdPipelineBarrier(cb,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&mb,0,NULL,0,NULL); VKCHECK(vkEndCommandBuffer(cb)); VkFenceCreateInfo fi={VK_STRUCTURE_TYPE_FENCE_CREATE_INFO,NULL,0}; VkFence fence; VKCHECK(vkCreateFence(device,&fi,NULL,&fence)); VkSubmitInfo si={VK_STRUCTURE_TYPE_SUBMIT_INFO,NULL,0,NULL,NULL,1,&cb,0,NULL}; VKCHECK(vkQueueSubmit(queue,1,&si,fence)); VKCHECK(vkWaitForFences(device,1,&fence,VK_TRUE,3000000000ULL)); uint32_t *out; VKCHECK(vkMapMemory(device,mem,0,N*4u,0,(void**)&out)); uint64_t sum=0; for(uint32_t i=0;i<N;i++){uint32_t want=i*3u+7u;if(out[i]!=want){fprintf(stderr,"mismatch[%u]=%u want=%u\n",i,out[i],want);return 4;}sum+=out[i];} vkUnmapMemory(device,mem); printf("checksum=%llu PASS\n",(unsigned long long)sum); vkDestroyFence(device,fence,NULL); vkFreeCommandBuffers(device,cp,1,&cb); vkDestroyCommandPool(device,cp,NULL); vkDestroyDescriptorPool(device,pool,NULL); vkDestroyPipeline(device,pipe,NULL); vkDestroyPipelineLayout(device,pl,NULL); vkDestroyShaderModule(device,sm,NULL); vkDestroyDescriptorSetLayout(device,sl,NULL); vkDestroyBuffer(device,buf,NULL); vkFreeMemory(device,mem,NULL); vkDestroyDevice(device,NULL); vkDestroyInstance(instance,NULL); dlclose(lib); return 0;
}
