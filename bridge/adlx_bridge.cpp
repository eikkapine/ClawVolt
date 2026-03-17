// =============================================================================
// ClawVolt ADLX Bridge — built against actual SDK headers (verified 2025-03)
//
// IADLXGPUTuningServices methods confirmed from IGPUTuning.h:
//   IsSupportedManualGFXTuning, GetManualGFXTuning, ResetToFactory
//
// ADLX_RESULT values confirmed from ADLXDefines.h:
//   ADLX_OK, ADLX_FAIL, ADLX_NOT_SUPPORTED, ADLX_INVALID_ARGS,
//   ADLX_BAD_VER, ADLX_UNKNOWN_INTERFACE, ADLX_TERMINATED,
//   ADLX_ADL_INIT_ERROR, ADLX_NOT_FOUND, ADLX_INVALID_OBJECT,
//   ADLX_ORPHAN_OBJECTS, ADLX_PENDING_OPERATION, ADLX_GPU_INACTIVE,
//   ADLX_GPU_IN_USE, ADLX_TIMEOUT_OPERATION, ADLX_NOT_ACTIVE
//
// NOTE: There is no SetManualGFXTuning on IADLXGPUTuningServices.
// Voltage writes are done directly via MGT2/MGT1 interfaces.
// --set-auto calls ResetToFactory to restore default GPU state.
// =============================================================================

#include "SDK/ADLXHelper/Windows/Cpp/ADLXHelper.h"
#include "SDK/Include/IPerformanceMonitoring.h"
#include "SDK/Include/IGPUTuning.h"
#include "SDK/Include/IGPUManualGFXTuning.h"

#include <iostream>
#include <string>
#include <cstdlib>

using namespace adlx;
static ADLXHelper g_ADLXHelp;

// ── Result codes from ADLXDefines.h ──────────────────────────────────────────
const char* ResultStr(ADLX_RESULT r)
{
    switch (r)
    {
        case ADLX_OK:               return "OK";
        case ADLX_ALREADY_ENABLED:  return "ALREADY_ENABLED";
        case ADLX_FAIL:             return "FAIL";
        case ADLX_INVALID_ARGS:     return "INVALID_ARGS";
        case ADLX_BAD_VER:          return "BAD_VER";
        case ADLX_UNKNOWN_INTERFACE:return "UNKNOWN_INTERFACE";
        case ADLX_TERMINATED:       return "TERMINATED";
        case ADLX_ADL_INIT_ERROR:   return "ADL_INIT_ERROR";
        case ADLX_NOT_FOUND:        return "NOT_FOUND";
        case ADLX_INVALID_OBJECT:   return "INVALID_OBJECT";
        case ADLX_ORPHAN_OBJECTS:   return "ORPHAN_OBJECTS";
        case ADLX_NOT_SUPPORTED:    return "NOT_SUPPORTED";
        case ADLX_PENDING_OPERATION:return "PENDING_OPERATION";
        case ADLX_GPU_INACTIVE:     return "GPU_INACTIVE";
        case ADLX_GPU_IN_USE:       return "GPU_IN_USE";
        case ADLX_TIMEOUT_OPERATION:return "TIMEOUT_OPERATION";
        case ADLX_NOT_ACTIVE:       return "NOT_ACTIVE";
        default:                    return "UNKNOWN";
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

bool GetFirstGPU(IADLXGPUPtr& gpu)
{
    IADLXGPUListPtr gpus;
    ADLX_RESULT res = g_ADLXHelp.GetSystemServices()->GetGPUs(&gpus);
    if (ADLX_FAILED(res) || gpus->Empty())
    {
        std::cerr << "ERROR: GetGPUs failed (" << ResultStr(res) << ")" << std::endl;
        return false;
    }
    if (ADLX_FAILED(gpus->At(gpus->Begin(), &gpu)))
    {
        std::cerr << "ERROR: No GPU found" << std::endl;
        return false;
    }
    return true;
}

bool GetTuningSvc(IADLXGPUTuningServicesPtr& svc)
{
    ADLX_RESULT res = g_ADLXHelp.GetSystemServices()->GetGPUTuningServices(&svc);
    if (ADLX_FAILED(res))
    {
        std::cerr << "ERROR: GetGPUTuningServices (" << ResultStr(res) << ")" << std::endl;
        return false;
    }
    return true;
}

// Get the manual GFX tuning interface for a GPU.
// Returns false and prints error if not supported or unavailable.
bool GetManualGFXIfc(IADLXGPUPtr& gpu, IADLXGPUTuningServicesPtr& svc, IADLXInterfacePtr& ifc)
{
    // Check support first (IsSupportedManualGFXTuning — confirmed in IGPUTuning.h)
    adlx_bool supported = false;
    svc->IsSupportedManualGFXTuning(gpu, &supported);
    if (!supported)
    {
        std::cerr << "ERROR: Manual GFX tuning not supported on this GPU" << std::endl;
        return false;
    }

    ADLX_RESULT res = svc->GetManualGFXTuning(gpu, &ifc);
    if (ADLX_FAILED(res) || !ifc)
    {
        std::cerr << "ERROR: GetManualGFXTuning (" << ResultStr(res) << ")" << std::endl;
        return false;
    }
    return true;
}

// ── --get-clock ───────────────────────────────────────────────────────────────

int CmdGetClock()
{
    IADLXGPUPtr gpu;
    if (!GetFirstGPU(gpu)) return 1;

    IADLXPerformanceMonitoringServicesPtr perfSvc;
    ADLX_RESULT res = g_ADLXHelp.GetSystemServices()->GetPerformanceMonitoringServices(&perfSvc);
    if (ADLX_FAILED(res))
    {
        std::cerr << "ERROR: GetPerformanceMonitoringServices (" << ResultStr(res) << ")" << std::endl;
        return 1;
    }

    IADLXGPUMetricsPtr metrics;
    res = perfSvc->GetCurrentGPUMetrics(gpu, &metrics);
    if (ADLX_FAILED(res))
    {
        std::cerr << "ERROR: GetCurrentGPUMetrics (" << ResultStr(res) << ")" << std::endl;
        return 1;
    }

    adlx_int clockMHz = 0;
    res = metrics->GPUClockSpeed(&clockMHz);
    if (ADLX_FAILED(res))
    {
        std::cerr << "ERROR: GPUClockSpeed (" << ResultStr(res) << ")" << std::endl;
        return 1;
    }

    std::cout << "CLOCK:" << clockMHz << std::endl;
    return 0;
}

// ── --info ────────────────────────────────────────────────────────────────────

int CmdInfo()
{
    IADLXGPUPtr gpu;
    if (!GetFirstGPU(gpu)) return 1;

    const char* name = nullptr;
    gpu->Name(&name);
    std::cout << "GPU_NAME:" << (name ? name : "unknown") << std::endl;

    IADLXGPUTuningServicesPtr svc;
    if (!GetTuningSvc(svc)) return 1;

    adlx_bool b = false;
    svc->IsSupportedManualGFXTuning(gpu, &b);
    std::cout << "TUNING_MANUAL_GFX_SUPPORTED:" << b << std::endl;
    svc->IsSupportedManualVRAMTuning(gpu, &b);
    std::cout << "TUNING_MANUAL_VRAM_SUPPORTED:" << b << std::endl;
    svc->IsSupportedManualFanTuning(gpu, &b);
    std::cout << "TUNING_MANUAL_FAN_SUPPORTED:" << b << std::endl;

    adlx_bool isFactory = false;
    svc->IsAtFactory(gpu, &isFactory);
    std::cout << "TUNING_AT_FACTORY:" << isFactory << std::endl;

    IADLXInterfacePtr ifc;
    if (!GetManualGFXIfc(gpu, svc, ifc)) return 0; // non-fatal for --info

    // MGT1: multi-point VF curve
    IADLXManualGraphicsTuning1Ptr mgt1;
    ADLX_RESULT res = ifc->QueryInterface(IADLXManualGraphicsTuning1::IID(), (void**)&mgt1);
    std::cout << "INTERFACE_MGT1:" << ADLX_SUCCEEDED(res) << std::endl;
    if (ADLX_SUCCEEDED(res) && mgt1)
    {
        IADLXManualTuningStateListPtr states;
        if (ADLX_SUCCEEDED(mgt1->GetGPUTuningStates(&states)) && states)
        {
            adlx_uint n = states->End() - states->Begin();
            std::cout << "MGT1_VF_POINTS:" << n << std::endl;
            for (adlx_uint i = states->Begin(); i != states->End(); ++i)
            {
                IADLXManualTuningStatePtr st;
                if (ADLX_SUCCEEDED(states->At(i, &st)))
                {
                    adlx_int freq = 0, volt = 0;
                    st->GetFrequency(&freq);
                    st->GetVoltage(&volt);
                    std::cout << "MGT1_P" << i << ":freq=" << freq << ",volt=" << volt << std::endl;
                }
            }
            ADLX_IntRange fr = {}, vr = {};
            if (ADLX_SUCCEEDED(mgt1->GetGPUTuningRanges(&fr, &vr)))
            {
                std::cout << "MGT1_FREQ_RANGE:" << fr.minValue << "-" << fr.maxValue << ",step=" << fr.step << std::endl;
                std::cout << "MGT1_VOLT_RANGE:" << vr.minValue << "-" << vr.maxValue << ",step=" << vr.step << std::endl;
            }
        }
    }

    // MGT2: single voltage — typical RDNA4
    IADLXManualGraphicsTuning2Ptr mgt2;
    res = ifc->QueryInterface(IADLXManualGraphicsTuning2::IID(), (void**)&mgt2);
    std::cout << "INTERFACE_MGT2:" << ADLX_SUCCEEDED(res) << std::endl;
    if (ADLX_SUCCEEDED(res) && mgt2)
    {
        adlx_int volt = 0, minF = 0, maxF = 0;
        ADLX_IntRange vr = {}, mfr = {};
        mgt2->GetGPUVoltage(&volt);
        mgt2->GetGPUMinFrequency(&minF);
        mgt2->GetGPUMaxFrequency(&maxF);
        mgt2->GetGPUVoltageRange(&vr);
        mgt2->GetGPUMaxFrequencyRange(&mfr);
        std::cout << "MGT2_VOLT:" << volt << std::endl;
        std::cout << "MGT2_MIN_FREQ:" << minF << std::endl;
        std::cout << "MGT2_MAX_FREQ:" << maxF << std::endl;
        std::cout << "MGT2_VOLT_RANGE:" << vr.minValue << "-" << vr.maxValue << ",step=" << vr.step << std::endl;
        std::cout << "MGT2_MAX_FREQ_RANGE:" << mfr.minValue << "-" << mfr.maxValue << ",step=" << mfr.step << std::endl;
    }

    // MGT2_1: RDNA4 with defaults
    IADLXManualGraphicsTuning2_1Ptr mgt2_1;
    res = ifc->QueryInterface(IADLXManualGraphicsTuning2_1::IID(), (void**)&mgt2_1);
    std::cout << "INTERFACE_MGT2_1:" << ADLX_SUCCEEDED(res) << std::endl;
    if (ADLX_SUCCEEDED(res) && mgt2_1)
    {
        adlx_int dv = 0, dmn = 0, dmx = 0;
        mgt2_1->GetGPUVoltageDefault(&dv);
        mgt2_1->GetGPUMinFrequencyDefault(&dmn);
        mgt2_1->GetGPUMaxFrequencyDefault(&dmx);
        std::cout << "MGT2_1_DEFAULT_VOLT:" << dv << std::endl;
        std::cout << "MGT2_1_DEFAULT_MIN_FREQ:" << dmn << std::endl;
        std::cout << "MGT2_1_DEFAULT_MAX_FREQ:" << dmx << std::endl;
    }

    return 0;
}

// ── --set-auto ────────────────────────────────────────────────────────────────
// Restores factory/default tuning state via ResetToFactory
// (IADLXGPUTuningServices has no SetManualGFXTuning — confirmed from headers)

int CmdSetAuto()
{
    IADLXGPUPtr gpu;
    if (!GetFirstGPU(gpu)) return 1;
    IADLXGPUTuningServicesPtr svc;
    if (!GetTuningSvc(svc)) return 1;

    ADLX_RESULT res = svc->ResetToFactory(gpu);
    if (ADLX_FAILED(res))
    {
        std::cerr << "ERROR: ResetToFactory (" << ResultStr(res) << ")" << std::endl;
        return 1;
    }
    std::cout << "STATUS:FACTORY_DEFAULTS_RESTORED" << std::endl;
    return 0;
}

// ── --get-voltage ─────────────────────────────────────────────────────────────

int CmdGetVoltage()
{
    IADLXGPUPtr gpu;
    if (!GetFirstGPU(gpu)) return 1;
    IADLXGPUTuningServicesPtr svc;
    if (!GetTuningSvc(svc)) return 1;

    IADLXInterfacePtr ifc;
    if (!GetManualGFXIfc(gpu, svc, ifc)) return 1;

    // Try MGT2 first (RDNA4)
    IADLXManualGraphicsTuning2Ptr mgt2;
    if (ADLX_SUCCEEDED(ifc->QueryInterface(IADLXManualGraphicsTuning2::IID(), (void**)&mgt2)) && mgt2)
    {
        adlx_int volt = 0;
        mgt2->GetGPUVoltage(&volt);
        std::cout << "VOLTAGE_MV:" << volt << std::endl;
        std::cout << "INTERFACE:MGT2" << std::endl;
        return 0;
    }

    // Fallback MGT1
    IADLXManualGraphicsTuning1Ptr mgt1;
    if (ADLX_SUCCEEDED(ifc->QueryInterface(IADLXManualGraphicsTuning1::IID(), (void**)&mgt1)) && mgt1)
    {
        IADLXManualTuningStateListPtr states;
        if (ADLX_SUCCEEDED(mgt1->GetGPUTuningStates(&states)) && states)
        {
            for (adlx_uint i = states->Begin(); i != states->End(); ++i)
            {
                IADLXManualTuningStatePtr st;
                if (ADLX_SUCCEEDED(states->At(i, &st)))
                {
                    adlx_int freq = 0, volt = 0;
                    st->GetFrequency(&freq);
                    st->GetVoltage(&volt);
                    std::cout << "VF_" << i << ":freq=" << freq << ",volt=" << volt << std::endl;
                }
            }
            std::cout << "INTERFACE:MGT1" << std::endl;
            return 0;
        }
    }

    std::cerr << "ERROR: No compatible tuning interface found" << std::endl;
    return 1;
}

// ── --set-voltage <offsetMV> ──────────────────────────────────────────────────

int CmdSetVoltage(int offsetMV)
{
    if (offsetMV > 0)
    {
        std::cerr << "ERROR: Positive voltage offsets blocked for safety" << std::endl;
        return 1;
    }
    if (offsetMV < -200)
    {
        std::cerr << "ERROR: Offset below -200mV refused (hardware limit is -200mV on RX 9070 XT)" << std::endl;
        return 1;
    }

    IADLXGPUPtr gpu;
    if (!GetFirstGPU(gpu)) return 1;
    IADLXGPUTuningServicesPtr svc;
    if (!GetTuningSvc(svc)) return 1;

    IADLXInterfacePtr ifc;
    if (!GetManualGFXIfc(gpu, svc, ifc)) return 1;

    // ── Path A: MGT2_1 (RDNA4, has default voltage query) ────────────────
    IADLXManualGraphicsTuning2_1Ptr mgt2_1;
    if (ADLX_SUCCEEDED(ifc->QueryInterface(IADLXManualGraphicsTuning2_1::IID(), (void**)&mgt2_1)) && mgt2_1)
    {
        // On RX 9070 XT (RDNA4), the voltage value IS the offset directly.
        // Range is -200 to 0 (mV). 0 = stock, -200 = max undervolt.
        ADLX_IntRange vr = {};
        mgt2_1->GetGPUVoltageRange(&vr);

        adlx_int newVolt = offsetMV;
        // Clamp to hardware range
        if (vr.minValue != 0 || vr.maxValue != 0)
        {
            if (newVolt < vr.minValue) newVolt = vr.minValue;
            if (newVolt > vr.maxValue) newVolt = vr.maxValue;
        }

        ADLX_RESULT res = mgt2_1->SetGPUVoltage(newVolt);
        if (ADLX_FAILED(res))
        {
            std::cerr << "ERROR: SetGPUVoltage (MGT2_1) (" << ResultStr(res) << ")" << std::endl;
            return 1;
        }
        std::cout << "STATUS:VOLTAGE_OFFSET_APPLIED"
                  << ":offset=" << newVolt
                  << ":interface=MGT2_1" << std::endl;
        return 0;
    }

    // ── Path B: MGT2 (RDNA4 fallback, no defaults) ───────────────────────
    IADLXManualGraphicsTuning2Ptr mgt2;
    if (ADLX_SUCCEEDED(ifc->QueryInterface(IADLXManualGraphicsTuning2::IID(), (void**)&mgt2)) && mgt2)
    {
        adlx_int curVolt = 0;
        mgt2->GetGPUVoltage(&curVolt);

        ADLX_IntRange vr = {};
        mgt2->GetGPUVoltageRange(&vr);

        adlx_int newVolt = curVolt + offsetMV;
        if (vr.maxValue != 0)
        {
            if (newVolt < vr.minValue) newVolt = vr.minValue;
            if (newVolt > vr.maxValue) newVolt = vr.maxValue;
        }

        ADLX_RESULT res = mgt2->SetGPUVoltage(newVolt);
        if (ADLX_FAILED(res))
        {
            std::cerr << "ERROR: SetGPUVoltage (MGT2) (" << ResultStr(res) << ")" << std::endl;
            return 1;
        }
        std::cout << "STATUS:VOLTAGE_OFFSET_APPLIED"
                  << ":base=" << curVolt
                  << ":offset=" << offsetMV
                  << ":applied=" << newVolt
                  << ":interface=MGT2" << std::endl;
        return 0;
    }

    // ── Path C: MGT1 (VF curve, older GPUs) ──────────────────────────────
    IADLXManualGraphicsTuning1Ptr mgt1;
    if (ADLX_FAILED(ifc->QueryInterface(IADLXManualGraphicsTuning1::IID(), (void**)&mgt1)) || !mgt1)
    {
        std::cerr << "ERROR: No compatible tuning interface on this GPU" << std::endl;
        return 1;
    }

    IADLXManualTuningStateListPtr current, empty;
    if (ADLX_FAILED(mgt1->GetGPUTuningStates(&current)) || !current ||
        ADLX_FAILED(mgt1->GetEmptyGPUTuningStates(&empty)) || !empty)
    {
        std::cerr << "ERROR: Could not get tuning state lists" << std::endl;
        return 1;
    }

    ADLX_IntRange fr = {}, vr = {};
    mgt1->GetGPUTuningRanges(&fr, &vr);

    adlx_uint count = current->End() - current->Begin();
    for (adlx_uint i = 0; i < count; ++i)
    {
        IADLXManualTuningStatePtr src, dst;
        if (ADLX_FAILED(current->At(i + current->Begin(), &src))) continue;
        if (ADLX_FAILED(empty->At(i + empty->Begin(), &dst))) continue;

        adlx_int freq = 0, volt = 0;
        src->GetFrequency(&freq);
        src->GetVoltage(&volt);

        adlx_int newVolt = volt + offsetMV;
        if (vr.maxValue != 0)
        {
            if (newVolt < vr.minValue) newVolt = vr.minValue;
            if (newVolt > vr.maxValue) newVolt = vr.maxValue;
        }
        dst->SetFrequency(freq);
        dst->SetVoltage(newVolt);
    }

    adlx_int errIdx = 0;
    ADLX_RESULT res = mgt1->IsValidGPUTuningStates(empty, &errIdx);
    if (ADLX_FAILED(res))
    {
        std::cerr << "ERROR: IsValidGPUTuningStates at index " << errIdx
                  << " (" << ResultStr(res) << ")" << std::endl;
        return 1;
    }

    res = mgt1->SetGPUTuningStates(empty);
    if (ADLX_FAILED(res))
    {
        std::cerr << "ERROR: SetGPUTuningStates (" << ResultStr(res) << ")" << std::endl;
        return 1;
    }

    std::cout << "STATUS:VOLTAGE_OFFSET_APPLIED:offset=" << offsetMV
              << ":interface=MGT1" << std::endl;
    return 0;
}

// ── Entry point ───────────────────────────────────────────────────────────────

int main(int argc, char* argv[])
{
    if (argc < 2)
    {
        std::cout << "ClawVolt ADLX Bridge\n"
                  << "  --get-clock         Read current core clock in MHz\n"
                  << "  --get-voltage       Read current voltage state\n"
                  << "  --set-voltage <mV>  Apply voltage offset (e.g. -120)\n"
                  << "  --set-auto          Reset GPU to factory/default tuning\n"
                  << "  --info              Dump GPU capabilities\n";
        return 1;
    }

    ADLX_RESULT initRes = g_ADLXHelp.Initialize();
    if (ADLX_FAILED(initRes))
    {
        std::cerr << "ERROR: ADLX init failed (" << ResultStr(initRes) << ")\n"
                  << "Ensure AMD Adrenalin driver is installed.\n";
        return 1;
    }

    std::string cmd = argv[1];
    int result = 0;

    if      (cmd == "--get-clock")   result = CmdGetClock();
    else if (cmd == "--get-voltage") result = CmdGetVoltage();
    else if (cmd == "--set-voltage")
    {
        if (argc < 3)
        {
            std::cerr << "ERROR: --set-voltage requires a mV value (e.g. --set-voltage -120)\n";
            g_ADLXHelp.Terminate();
            return 1;
        }
        result = CmdSetVoltage(std::atoi(argv[2]));
    }
    else if (cmd == "--set-auto")    result = CmdSetAuto();
    else if (cmd == "--set-manual")
    {
        // No SetManualGFXTuning exists on IADLXGPUTuningServices.
        // Voltage writes via MGT2/MGT1 work directly without a mode switch.
        // This command is a no-op kept for Python controller compatibility.
        std::cout << "STATUS:MANUAL_MODE_ACTIVE" << std::endl;
        result = 0;
    }
    else if (cmd == "--info")        result = CmdInfo();
    else
    {
        std::cerr << "ERROR: Unknown command: " << cmd << "\n";
        result = 1;
    }

    g_ADLXHelp.Terminate();
    return result;
}
