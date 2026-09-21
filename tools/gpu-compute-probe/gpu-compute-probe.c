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
    const int transfer_only = getenv("S22_GPU_TRANSFER_ONLY") != NULL;
    const int internal_readback = getenv("S22_GPU_INTERNAL_READBACK_ONLY") != NULL;
    const int prepare_only = getenv("S22_GPU_PREPARE_ONLY") != NULL;
    const int record_only = getenv("S22_GPU_RECORD_ONLY") != NULL;
    const int special_shader = getenv("S22_GPU_LITERAL_DESCRIPTOR_DIAGNOSTIC") != NULL ||
                               getenv("S22_GPU_SMEM_ZERO_DIAGNOSTIC") != NULL;
    if (transfer_only + internal_readback + prepare_only + record_only > 1) {
        fprintf(stderr, "S22_GPU special modes cannot be combined with transfer, prepare, or record modes\n");
        return 2;
    }
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
    D(vkDestroyDevice); D(vkGetDeviceQueue); D(vkCreateBuffer); D(vkGetBufferMemoryRequirements); D(vkAllocateMemory); D(vkBindBufferMemory); D(vkMapMemory); D(vkUnmapMemory); D(vkCreateDescriptorSetLayout); D(vkCreatePipelineLayout); D(vkCreateShaderModule); D(vkCreateComputePipelines); D(vkCreateDescriptorPool); D(vkAllocateDescriptorSets); D(vkUpdateDescriptorSets); D(vkCreateCommandPool); D(vkAllocateCommandBuffers); D(vkBeginCommandBuffer); D(vkCmdBindPipeline); D(vkCmdBindDescriptorSets); D(vkCmdDispatch); D(vkCmdFillBuffer); D(vkCmdPipelineBarrier); D(vkEndCommandBuffer); D(vkCreateFence); D(vkQueueSubmit); D(vkWaitForFences); D(vkDestroyFence); D(vkFreeCommandBuffers); D(vkDestroyCommandPool); D(vkDestroyDescriptorPool); D(vkDestroyPipeline); D(vkDestroyPipelineLayout); D(vkDestroyShaderModule); D(vkDestroyDescriptorSetLayout); D(vkDestroyBuffer); D(vkFreeMemory);
    VkQueue queue; vkGetDeviceQueue(device, qfam, 0, &queue);
    if (transfer_only) {
        VkBufferCreateInfo tbci = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, N * 4u,
                                    VK_BUFFER_USAGE_TRANSFER_DST_BIT, VK_SHARING_MODE_EXCLUSIVE, 1, &qfam };
        VkBuffer tbuf = VK_NULL_HANDLE; VkDeviceMemory tmem = VK_NULL_HANDLE; VkCommandPool tcp = VK_NULL_HANDLE;
        VkCommandBuffer tcb = VK_NULL_HANDLE; VkFence tfence = VK_NULL_HANDLE; int result = 1;
        int submitted = 0; const char *stage = "vkCreateBuffer";
        VkResult tr = vkCreateBuffer(device, &tbci, NULL, &tbuf);
        if (tr == VK_SUCCESS) {
            VkMemoryRequirements tmr; vkGetBufferMemoryRequirements(device, tbuf, &tmr);
            VkPhysicalDeviceMemoryProperties tmp; vkGetPhysicalDeviceMemoryProperties(phys, &tmp);
            uint32_t tmi = UINT32_MAX;
            for (uint32_t i = 0; i < tmp.memoryTypeCount; i++)
                if ((tmr.memoryTypeBits & (1u << i)) &&
                    (tmp.memoryTypes[i].propertyFlags & (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) ==
                    (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) { tmi = i; break; }
            if (tmi != UINT32_MAX) {
                VkMemoryAllocateInfo tmai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, tmr.size, tmi };
                stage = "vkAllocateMemory";
                tr = vkAllocateMemory(device, &tmai, NULL, &tmem);
            } else tr = VK_ERROR_FEATURE_NOT_PRESENT;
        }
        if (tr == VK_SUCCESS) { stage = "vkBindBufferMemory"; tr = vkBindBufferMemory(device, tbuf, tmem, 0); }
        if (tr == VK_SUCCESS) {
            uint32_t *initial = NULL; stage = "vkMapMemory(initial)";
            tr = vkMapMemory(device, tmem, 0, N * 4u, 0, (void **)&initial);
            if (tr == VK_SUCCESS) { memset(initial, 0xA5, N * 4u); vkUnmapMemory(device, tmem); }
        }
        if (tr == VK_SUCCESS) {
            VkCommandPoolCreateInfo tcpci = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, 0, qfam };
            stage = "vkCreateCommandPool";
            tr = vkCreateCommandPool(device, &tcpci, NULL, &tcp);
        }
        if (tr == VK_SUCCESS) {
            VkCommandBufferAllocateInfo tcai = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, tcp, VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1 };
            stage = "vkAllocateCommandBuffers";
            tr = vkAllocateCommandBuffers(device, &tcai, &tcb);
        }
        if (tr == VK_SUCCESS) {
            VkCommandBufferBeginInfo tcbi = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, NULL, VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT, NULL };
            stage = "vkBeginCommandBuffer";
            tr = vkBeginCommandBuffer(tcb, &tcbi);
            if (tr == VK_SUCCESS) {
                vkCmdFillBuffer(tcb, tbuf, 0, N * 4u, 0x12345678u);
                VkMemoryBarrier tmb = { VK_STRUCTURE_TYPE_MEMORY_BARRIER, NULL, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_HOST_READ_BIT };
                vkCmdPipelineBarrier(tcb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &tmb, 0, NULL, 0, NULL);
                stage = "vkEndCommandBuffer";
                tr = vkEndCommandBuffer(tcb);
            }
        }
        if (tr == VK_SUCCESS) {
            VkFenceCreateInfo tfci = { VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, NULL, 0 };
            stage = "vkCreateFence";
            tr = vkCreateFence(device, &tfci, NULL, &tfence);
        }
        if (tr == VK_SUCCESS) {
            VkSubmitInfo tsi = { VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 0, NULL, NULL, 1, &tcb, 0, NULL };
            stage = "vkQueueSubmit";
            tr = vkQueueSubmit(queue, 1, &tsi, tfence);
            submitted = tr == VK_SUCCESS;
        }
        if (tr == VK_SUCCESS) { stage = "vkWaitForFences(3s)"; tr = vkWaitForFences(device, 1, &tfence, VK_TRUE, 3000000000ULL); }
        if (tr != VK_SUCCESS && submitted) {
            fprintf(stderr, "TRANSFER_ONLY %s failed: %d; submission completion unproven, leaving Vulkan objects for process exit\n", stage, tr);
            return 1;
        }
        if (tr == VK_SUCCESS) {
            uint32_t *tout = NULL; stage = "vkMapMemory(readback)"; tr = vkMapMemory(device, tmem, 0, N * 4u, 0, (void **)&tout);
            if (tr == VK_SUCCESS) {
                uint64_t checksum = 1469598103934665603ULL;
                result = 0;
                for (uint32_t i = 0; i < N; i++) {
                    if (tout[i] != 0x12345678u) { fprintf(stderr, "TRANSFER_ONLY mismatch[%u]=0x%08x\n", i, tout[i]); result = 4; break; }
                    checksum = (checksum ^ tout[i]) * 1099511628211ULL;
                }
                vkUnmapMemory(device, tmem);
                if (!result) printf("TRANSFER_ONLY checksum=0x%016llx PASS\n", (unsigned long long)checksum);
            }
        }
        if (tr != VK_SUCCESS) { fprintf(stderr, "TRANSFER_ONLY %s failed: %d\n", stage, tr); result = 1; }
        if (tfence) vkDestroyFence(device, tfence, NULL);
        if (tcb) vkFreeCommandBuffers(device, tcp, 1, &tcb);
        if (tcp) vkDestroyCommandPool(device, tcp, NULL);
        if (tbuf) vkDestroyBuffer(device, tbuf, NULL);
        if (tmem) vkFreeMemory(device, tmem, NULL);
        vkDestroyDevice(device, NULL); vkDestroyInstance(instance, NULL); dlclose(lib);
        return result;
    }
    FILE *f = fopen(argv[1], "rb"); if (!f) { perror(argv[1]); return 1; } fseek(f, 0, SEEK_END); long sz = ftell(f); rewind(f); if (sz < 4 || sz > 1024*1024 || (sz & 3)) { fprintf(stderr, "invalid SPIR-V size: %ld\n", sz); return 1; } uint32_t *code = malloc((size_t)sz); if (!code || fread(code, 1, (size_t)sz, f) != (size_t)sz) return 1; fclose(f); if (code[0] != 0x07230203u) { fprintf(stderr, "bad SPIR-V magic\n"); return 1; }
    VkShaderModuleCreateInfo smi = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, NULL, 0, (size_t)sz, code }; VkShaderModule sm; VKCHECK(vkCreateShaderModule(device, &smi, NULL, &sm)); free(code);
    VkBufferUsageFlags usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
    if (internal_readback) usage |= VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
    VkBufferCreateInfo bci = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, N*4u, usage, VK_SHARING_MODE_EXCLUSIVE, 1, &qfam }; VkBuffer buf; VKCHECK(vkCreateBuffer(device, &bci, NULL, &buf));
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
    uint32_t *initial; VKCHECK(vkMapMemory(device,mem,0,N*4u,0,(void**)&initial)); memset(initial,0xA5,N*4u);
    vkUnmapMemory(device,mem);
    VkCommandPoolCreateInfo cpci={VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,NULL,0,qfam}; VkCommandPool cp; VKCHECK(vkCreateCommandPool(device,&cpci,NULL,&cp));
    VkCommandBufferAllocateInfo cai={VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,NULL,cp,VK_COMMAND_BUFFER_LEVEL_PRIMARY,1}; VkCommandBuffer cb; VKCHECK(vkAllocateCommandBuffers(device,&cai,&cb)); VkCommandBufferBeginInfo cbi={VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,NULL,VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,NULL}; VKCHECK(vkBeginCommandBuffer(cb,&cbi)); vkCmdBindPipeline(cb,VK_PIPELINE_BIND_POINT_COMPUTE,pipe); vkCmdBindDescriptorSets(cb,VK_PIPELINE_BIND_POINT_COMPUTE,pl,0,1,&ds,0,NULL);
    if (internal_readback) {
        /* Only the paired diagnostic ICD intercepts this call and replaces it
         * with GPU copies from its actual descriptor and shader allocations. */
        vkCmdDispatch(cb,4,1,1);
        VkMemoryBarrier mb={VK_STRUCTURE_TYPE_MEMORY_BARRIER,NULL,VK_ACCESS_TRANSFER_WRITE_BIT,VK_ACCESS_HOST_READ_BIT}; vkCmdPipelineBarrier(cb,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&mb,0,NULL,0,NULL);
    } else {
        vkCmdDispatch(cb,4,1,1);
        VkMemoryBarrier mb={VK_STRUCTURE_TYPE_MEMORY_BARRIER,NULL,VK_ACCESS_SHADER_WRITE_BIT,VK_ACCESS_HOST_READ_BIT}; vkCmdPipelineBarrier(cb,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&mb,0,NULL,0,NULL);
    }
    VKCHECK(vkEndCommandBuffer(cb)); VkFenceCreateInfo fi={VK_STRUCTURE_TYPE_FENCE_CREATE_INFO,NULL,0}; VkFence fence; VKCHECK(vkCreateFence(device,&fi,NULL,&fence)); VkSubmitInfo si={VK_STRUCTURE_TYPE_SUBMIT_INFO,NULL,0,NULL,NULL,1,&cb,0,NULL}; VKCHECK(vkQueueSubmit(queue,1,&si,fence)); VKCHECK(vkWaitForFences(device,1,&fence,VK_TRUE,3000000000ULL)); uint32_t *out; VKCHECK(vkMapMemory(device,mem,0,N*4u,0,(void**)&out));
    if (internal_readback) {
        printf("INTERNAL_READBACK DIAGNOSTIC ONLY descriptor=%08x,%08x,%08x,%08x shader=", out[0], out[1], out[2], out[3]);
        for (uint32_t i=0; i<32; i++) printf("%s%08x", i ? "," : "", out[16+i]);
        printf(" registers=");
        for (uint32_t i=0; i<10; i++) printf("%s%08x", i ? "," : "", out[48+i]);
        printf("; NOT COMPUTE PASS\n");
    } else {
        uint64_t sum=0; for(uint32_t i=0;i<N;i++){uint32_t want=i*3u+7u;if(out[i]!=want){fprintf(stderr,"mismatch[%u]=%u want=%u\n",i,out[i],want);return 4;}sum+=out[i];}
        if (special_shader) printf("SPECIAL_SHADER_DIAGNOSTIC checksum=%llu MATCH; NOT GENERAL COMPUTE PASS\n",(unsigned long long)sum);
        else printf("checksum=%llu PASS\n",(unsigned long long)sum);
    }
    vkUnmapMemory(device,mem); vkDestroyFence(device,fence,NULL); vkFreeCommandBuffers(device,cp,1,&cb); vkDestroyCommandPool(device,cp,NULL); vkDestroyDescriptorPool(device,pool,NULL); vkDestroyPipeline(device,pipe,NULL); vkDestroyPipelineLayout(device,pl,NULL); vkDestroyShaderModule(device,sm,NULL); vkDestroyDescriptorSetLayout(device,sl,NULL); vkDestroyBuffer(device,buf,NULL); vkFreeMemory(device,mem,NULL); vkDestroyDevice(device,NULL); vkDestroyInstance(instance,NULL); dlclose(lib); return 0;
}
