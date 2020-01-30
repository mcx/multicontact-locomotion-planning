import multicontact_api
from multicontact_api import ContactSequence, ContactPhase, ContactPatch
from pinocchio import SE3, Quaternion
from mlp.utils.util import SE3FromConfig,  computeContactNormal, JointPlacementForEffector, rootOrientationFromFeetPlacement, copyPhaseInitToFinal
from mlp.utils.util import computeEffectorTranslationBetweenStates, computeEffectorRotationBetweenStates
import numpy as np
import types
from hpp.corbaserver.rbprm.rbprmstate import State, StateHelper
from math import isnan, ceil
multicontact_api.switchToNumpyArray()


def addPhaseFromConfig(fb, v, cs, q, limbsInContact, t_init = -1):
    multicontact_api.switchToNumpyArray() # FIXME : why is it required to add it again here ?
    phase = ContactPhase()
    phase.q_init =np.array(q)
    if v:
        v(q)
    fb.setCurrentConfig(q)
    com = np.array(fb.getCenterOfMass())
    if t_init > 0:
        phase.timeInitial = 0.
    phase.c_init = com.copy()
    phase.c_final = com.copy()
    for limb in limbsInContact:
        eeName = fb.dict_limb_joint[limb]
        q_j = fb.getJointPosition(eeName)
        placement = SE3FromConfig(q_j).act(fb.dict_offset[eeName])
        patch = ContactPatch(placement) # TODO set friction / other parameters here
        phase.addContact(eeName,patch)
    cs.append(phase)


def computeCenterOfSupportPolygonFromState(s):
    com = np.zeros(3)
    numContacts = float(len(s.getLimbsInContact()))
    for limbId in s.getLimbsInContact():
        com += np.array(s.getCenterOfContactForLimb(limbId)[0])
    com /= numContacts
    com[2] += s.fullBody.DEFAULT_COM_HEIGHT
    return com.tolist()

def computeCenterOfSupportPolygonFromPhase(phase, DEFAULT_HEIGHT):
    com = np.zeros(3)
    for patch in phase.contactPatches().values():
        com += patch.placement.translation
    com /= phase.numContacts()
    com[2] += DEFAULT_HEIGHT
    return com


def projectCoMInSupportPolygon(s):
    desiredCOM = computeCenterOfSupportPolygonFromState(s)
    # print "try to project state to com position : ",desiredCOM
    success = False
    maxIt = 20
    #print "project state to com : ", desiredCOM
    q_save = s.q()[::]
    while not success and maxIt > 0:
        success = s.fullBody.projectStateToCOM(s.sId, desiredCOM, maxNumSample=0)
        maxIt -= 1
        desiredCOM[2] -= 0.005
    #print "success = ", success
    #print "result = ", s.q()
    if success and isnan(s.q()[0]):  # FIXME why does it happen ?
        success = False
        s.setQ(q_save)
    return success


def generateConfigFromPhase(fb, phase, projectCOM=False):
    fb.usePosturalTaskContactCreation(False)
    effectorsInContact = phase.effectorsInContact()
    contacts = []  # contacts should contains the limb names, not the effector names
    list_effector = list(fb.dict_limb_joint.values())
    for eeName in effectorsInContact:
        contacts += [list(fb.dict_limb_joint.keys())[list_effector.index(eeName)]]
    #q = phase.q_init.tolist() # should be the correct config for the previous phase, if used only from high level helper methods
    q = fb.referenceConfig[::] + [0] * 6  # FIXME : more generic !
    root = computeCenterOfSupportPolygonFromPhase(phase, fb).tolist()
    q[0:2] = root[0:2]
    q[2] += root[2] - fb.DEFAULT_COM_HEIGHT
    quat = Quaternion(rootOrientationFromFeetPlacement(phase, None)[0].rotation)
    q[3:7] = [quat.x, quat.y, quat.z, quat.w]
    # create state in fullBody :
    state = State(fb, q=q, limbsIncontact=contacts)
    # check if q is consistent with the contact placement in the phase :
    fb.setCurrentConfig(q)
    for limbId in contacts:
        eeName = fb.dict_limb_joint[limbId]
        placement_fb = SE3FromConfig(fb.getJointPosition(eeName))
        placement_phase = JointPlacementForEffector(phase, eeName, fb)
        if placement_fb != placement_phase:  # add a threshold instead of 0 ? how ?
            # need to project the new contact :
            placement = phase.contactPatch(eeName).placement
            p = placement.translation.tolist()
            n = computeContactNormal(placement).tolist()
            state, success = StateHelper.addNewContact(state, limbId, p, n, 1000)
            if not success:
                print("Cannot project the configuration to contact, for effector : ", eeName)
                return state.q()
            if projectCOM:
                success = projectCoMInSupportPolygon(state)
                if not success:
                    print("cannot project com to the middle of the support polygon.")
    phase.q_init = np.array(state.q())

    return state.q()


def setFinalState(cs, com=None, q=None):
    phase = cs.contactPhases[-1]
    if q is not None:
        phase.q_end = np.array(q)
    if com is None:
        com_x = 0.
        com_y = 0.
        for patch in phase.contactPatches().values():
            com_x += patch.placement.translation[0]
            com_y += patch.placement.translation[1]
        com_x /= phase.numContacts()
        com_y /= phase.numContacts()
        com_z = phase.c_init[2]
        com = np.array([com_x, com_y, com_z])
    elif isinstance(com, list):
        com = np.array(com)
    copyPhaseInitToFinal(phase)
    phase.c_final = com


# generate a walking motion from the last phase in the contact sequence.
# the contacts will be moved in the order of the 'gait' list. With the first one move only of half the stepLength
# TODO : make it generic ! it's currently limited to motion in the x direction
def walk(fb, cs, distance, stepLength, gait, duration_ss = -1 , duration_ds = -1):
    fb.usePosturalTaskContactCreation(True)
    prev_phase = cs.contactPhases[-1]
    for limb in gait:
        eeName = fb.dict_limb_joint[limb]
        assert prev_phase.isEffectorInContact(eeName), "All limbs in gait should be in contact in the first phase"
    isFirst = True
    reached = False
    firstContactReachedGoal = False
    remainingDistance = distance
    while remainingDistance >= 0:
        for k, limb in enumerate(gait):
            eeName = fb.dict_limb_joint[limb]
            if isFirst:
                length = stepLength / 2.
                isFirst = False
            else:
                length = stepLength
            if k == 0:
                if length > (remainingDistance + stepLength / 2.):
                    length = remainingDistance + stepLength / 2.
                    firstContactReachedGoal = True
            else:
                if length > remainingDistance:
                    length = remainingDistance
            transform = SE3.Identity()
            transform.translation = np.array([length, 0, 0])
            cs.moveEffectorOf(eeName, transform, duration_ds, duration_ss)
        remainingDistance -= stepLength
    if not firstContactReachedGoal:
        transform = SE3.Identity()
        transform.translation  = [stepLength / 2., 0, 0]
        cs.moveEffectorOf(fb.dict_limb_joint[gait[0]], transform, duration_ds, duration_ss)
    q_end = fb.referenceConfig[::] + [0] * 6
    q_end[0] += distance
    fb.setCurrentConfig(q_end)
    com = fb.getCenterOfMass()
    setFinalState(cs, com, q=q_end)
    fb.usePosturalTaskContactCreation(False)


def computePhasesTimings(cs, cfg):
    current_t = cs.contactPhases[0].timeInitial
    if current_t < 0:
        current_t = 0.

    for pid,phase in enumerate(cs.contactPhases):
        duration = 0
        if phase.numContacts() == 1:
            duration = cfg.DURATION_SS
        if phase.numContacts() == 2:
            duration = cfg.DURATION_DS
        if phase.numContacts() == 3:
            duration = cfg.DURATION_TS
        if phase.numContacts() == 4:
            duration = cfg.DURATION_QS
        if phase.numContacts() > 4:
            raise Exception("Case not implemented")
        if pid == 0:
            duration = cfg.DURATION_INIT
        if pid == (cs.size() - 1):
            duration = cfg.DURATION_FINAL
        # Adjust duration if needed to respect bound on effector velocity
        duration_feet_trans = 0.
        duration_feet_rot = 0.
        if pid < cs.size() - 1:
            dist_feet = computeEffectorTranslationBetweenStates(cs, pid)
            if dist_feet > 0.:
                duration_feet_trans = (2. * cfg.EFF_T_DELAY + 2. * cfg.EFF_T_PREDEF) + dist_feet / cfg.FEET_MAX_VEL
            rot_feet = computeEffectorRotationBetweenStates(cs, pid)
            if rot_feet > 0.:
                duration_feet_rot = (2. * cfg.EFF_T_DELAY + 2. * cfg.EFF_T_PREDEF) + rot_feet / cfg.FEET_MAX_ANG_VEL
            duration_feet = max(duration_feet_trans, duration_feet_rot)
            # Make it a multiple of solver_dt :
            if duration_feet > 0.:
                duration_feet = ceil(duration_feet / cfg.SOLVER_DT) * cfg.SOLVER_DT
            if False:
                print("for phase : ", pid)
                print("dist_feet            : ", dist_feet)
                print("duration translation : ", duration_feet_trans)
                print("rot_feet             : ", rot_feet)
                print("duration rotation    : ", duration_feet_rot)
                print("duration complete    : ", duration_feet)
            duration = max(duration, duration_feet)
        phase.timeInitial = current_t
        phase.duration = duration
        current_t = phase.timeFinal
    return cs



def computePhasesCOMValues(cs,DEFAULT_HEIGHT):
    for pid,phase in enumerate(cs.contactPhases):
        if not phase.c_init.any():
            # this value is uninitialized
            phase.c_init = computeCenterOfSupportPolygonFromPhase(phase,DEFAULT_HEIGHT)
        if pid > 0:
            cs.contactPhases[pid-1].c_final = phase.c_init
    if not cs.contactPhases[-1].c_final.any():
        # this value is uninitialized
        cs.contactPhases[-1].c_final = cs.contactPhases[-1].c_init
    return cs

def computePhasesConfigurations(cs, fb):
    for pid, phase in enumerate(cs.contactPhases):
        if not phase.q_init.any():
            generateConfigFromPhase(fb, phase, projectCOM=True)
        if pid > 0:
            cs.contactPhases[pid-1].q_final = phase.q_init
    if not cs.contactPhases[-1].q_final.any():
        cs.contactPhases[-1].q_final =  cs.contactPhases[-1].q_init
    return cs